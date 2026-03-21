"""Pass 1 orchestration — global scene & camera calibration."""

from __future__ import annotations

import json
from pathlib import Path

from pbva_core.types import (
    BallColorModel,
    CourtGeometry,
    Pass1AcceptedOutput,
    Pass1CorrectionPayload,
    Pass1RawResult,
    StableBounds,
)
from pbva_pipeline.base import PassContext

from .background_plate import build_background_plate
from .detect_court import detect_court
from .detect_stable_bounds import detect_stable_bounds
from .infer_ball_color import infer_ball_color


class Pass1:
    name = "pass1"

    # ------------------------------------------------------------------
    # PipelinePass protocol
    # ------------------------------------------------------------------

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")

    def run(self, ctx: PassContext, progress=None) -> Pass1RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        video_path = ctx.video_path
        raw_dir = ctx.paths.pass_raw_dir

        # --- Stage 1: stable bounds ---
        progress.update(0.0, "detect_stable_bounds", "Scanning for camera motion…")
        progress.check_cancelled()
        bounds = detect_stable_bounds(video_path, ctx.video_duration_s)
        progress.update(0.15, "detect_stable_bounds", f"Stable: {bounds.in_time_s:.1f}s – {bounds.out_time_s:.1f}s")

        # --- Stage 2: background plate ---
        progress.update(0.15, "build_background_plate", "Sampling frames for median background…")
        progress.check_cancelled()
        bg_path = raw_dir / "median_background.png"

        def _bg_progress(frac: float, msg: str) -> None:
            progress.update(frac, "build_background_plate", msg)
            progress.check_cancelled()

        median_bg = build_background_plate(
            video_path,
            bounds.in_time_s,
            bounds.out_time_s,
            target_samples=300,
            output_path=bg_path,
            progress_callback=_bg_progress,
            progress_start=0.15,
            progress_end=0.55,
        )
        progress.update(0.55, "build_background_plate", f"Background saved to {bg_path.name}")

        # --- Stage 3: court detection ---
        progress.update(0.55, "detect_court", "Detecting court lines…")
        progress.check_cancelled()
        overlay_path = raw_dir / "court_overlay.png"
        court_geo, court_confidence = detect_court(median_bg, output_path=overlay_path)
        progress.update(0.75, "detect_court", f"Court confidence: {court_confidence:.2f}")

        # --- Stage 4: ball color profiling ---
        progress.update(0.75, "infer_ball_color", "Profiling ball color from moving blobs…")
        progress.check_cancelled()
        ball_color, color_confidence = infer_ball_color(
            video_path, median_bg, bounds.in_time_s, bounds.out_time_s
        )
        progress.update(0.90, "infer_ball_color", f"Ball color confidence: {color_confidence:.2f}")

        # --- Stage 5: write raw result JSON ---
        progress.update(0.90, "write_outputs", "Writing raw result…")
        result = Pass1RawResult(
            stable_bounds=bounds,
            court_geometry=court_geo,
            ball_color_model=ball_color,
            median_background_path=str(bg_path.relative_to(ctx.paths.project_root)),
            court_overlay_path=str(overlay_path.relative_to(ctx.paths.project_root)),
            confidence={"court": court_confidence, "ball_color": color_confidence},
        )
        result_path = raw_dir / "result.json"
        result_path.write_text(result.model_dump_json(indent=2))
        progress.update(1.0, "write_outputs", "Pass 1 complete")

        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass1RawResult) -> list[dict]:
        """Return list of artifact dicts to register in the DB."""
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = [
            {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
            {"role": "raw", "type": "png", "path": str(raw_dir / "median_background.png")},
            {"role": "raw", "type": "png", "path": str(raw_dir / "court_overlay.png")},
        ]
        return [a for a in artifacts if Path(a["path"]).exists()]

    def validate_corrections(self, payload: dict) -> Pass1CorrectionPayload:
        return Pass1CorrectionPayload.model_validate(payload)

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass1RawResult,
        corrections: Pass1CorrectionPayload | None,
        median_bg_artifact_id: str = "",
    ) -> Pass1AcceptedOutput:
        """Merge raw result with user corrections into the accepted output."""
        bounds = corrections.stable_bounds if corrections and corrections.stable_bounds else raw_result.stable_bounds
        court_geo = corrections.court_geometry if corrections and corrections.court_geometry else raw_result.court_geometry
        ball_color = corrections.ball_color_model if corrections and corrections.ball_color_model else raw_result.ball_color_model

        accepted = Pass1AcceptedOutput(
            stable_bounds=bounds,
            court_geometry=court_geo,
            ball_color_model=ball_color,
            median_background_artifact_id=median_bg_artifact_id,
            calibration_confidence=raw_result.confidence.get("court", 0.5),
        )

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))

        return accepted
