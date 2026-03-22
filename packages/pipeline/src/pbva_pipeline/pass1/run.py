"""Pass 1 orchestration — global scene & camera calibration."""

from __future__ import annotations

from pathlib import Path

from pbva_core.types import (
    CourtCorner,
    CourtGeometry,
    Pass1AcceptedOutput,
    Pass1CorrectionPayload,
    Pass1RawResult,
)
from pbva_pipeline.base import PassContext

from .scan import scan_video

# Background plate working resolution — must match BG_W/BG_H in the frontend.
_BG_W, _BG_H = 960, 540

# Default court corners as normalized screen coordinates, converted to pixel coords.
# These match the initial overlay shown in the Pass 1 review UI.
_DEFAULT_COURT = CourtGeometry(
    top_left=CourtCorner(x=0.35 * _BG_W, y=0.30 * _BG_H),
    top_right=CourtCorner(x=0.65 * _BG_W, y=0.30 * _BG_H),
    bottom_left=CourtCorner(x=0.05 * _BG_W, y=0.90 * _BG_H),
    bottom_right=CourtCorner(x=0.95 * _BG_W, y=0.90 * _BG_H),
)


class Pass1:
    name = "pass1"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")

    def run(self, ctx: PassContext, progress=None) -> Pass1RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        raw_dir = ctx.paths.pass_raw_dir
        bg_path = raw_dir / "median_background.png"

        # --- Single-pass scan: stable bounds + median background ---
        progress.update(0.0, "scan_video", "Scanning video…")
        progress.check_cancelled()

        def _scan_progress(frac: float, msg: str) -> None:
            progress.update(frac, "scan_video", msg)
            progress.check_cancelled()

        bounds, _ = scan_video(
            ctx.video_path,
            ctx.video_duration_s,
            target_samples=300,
            output_path=bg_path,
            progress_callback=_scan_progress,
            progress_start=0.0,
            progress_end=0.95,
        )
        progress.update(0.95, "scan_video",
                        f"Stable: {bounds.in_time_s:.1f}s – {bounds.out_time_s:.1f}s")

        # --- Write raw result JSON ---
        progress.update(0.95, "write_outputs", "Writing raw result…")
        result = Pass1RawResult(
            stable_bounds=bounds,
            median_background_path=str(bg_path.relative_to(ctx.paths.project_root)),
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
        progress.update(1.0, "write_outputs", "Pass 1 complete")

        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass1RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = [
            {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
            {"role": "raw", "type": "png", "path": str(raw_dir / "median_background.png")},
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
        bounds = (corrections.stable_bounds if corrections and corrections.stable_bounds
                  else raw_result.stable_bounds)
        court_geo = (corrections.court_geometry if corrections and corrections.court_geometry
                     else _DEFAULT_COURT)

        accepted = Pass1AcceptedOutput(
            stable_bounds=bounds,
            court_geometry=court_geo,
            median_background_artifact_id=median_bg_artifact_id,
        )

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))

        return accepted
