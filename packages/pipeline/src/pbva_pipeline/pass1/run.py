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

def _default_court(bg_w: int, bg_h: int) -> CourtGeometry:
    """Return default court corners at normalized positions in the background image."""
    return CourtGeometry(
        top_left=CourtCorner(x=0.35 * bg_w, y=0.30 * bg_h),
        top_right=CourtCorner(x=0.65 * bg_w, y=0.30 * bg_h),
        bottom_left=CourtCorner(x=0.05 * bg_w, y=0.90 * bg_h),
        bottom_right=CourtCorner(x=0.95 * bg_w, y=0.90 * bg_h),
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

        bounds, median_bg = scan_video(
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
        bg_h, bg_w = median_bg.shape[:2]
        result = Pass1RawResult(
            stable_bounds=bounds,
            median_background_path=str(bg_path.relative_to(ctx.paths.project_root)),
            bg_width=bg_w,
            bg_height=bg_h,
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
        bounds = raw_result.stable_bounds
        court_geo = (corrections.court_geometry if corrections and corrections.court_geometry
                     else _default_court(raw_result.bg_width, raw_result.bg_height))

        accepted = Pass1AcceptedOutput(
            stable_bounds=bounds,
            court_geometry=court_geo,
            median_background_artifact_id=median_bg_artifact_id,
            bg_width=raw_result.bg_width,
            bg_height=raw_result.bg_height,
        )

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))

        return accepted
