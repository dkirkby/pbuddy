"""Pass 2 orchestration — moving object detection via background subtraction."""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from pbva_core.types import (
    Pass1AcceptedOutput,
    Pass2AcceptedOutput,
    Pass2CorrectionPayload,
    Pass2RawResult,
)
from pbva_pipeline.base import PassContext

from .detect_blobs import DEFAULT_MAX_AREA, DEFAULT_MIN_AREA, DEFAULT_THRESHOLD, detect_blobs


class Pass2:
    name = "pass2"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")
        if not ctx.prior_accepted:
            raise ValueError("Pass 1 accepted output is required for Pass 2")

    def run(self, ctx: PassContext, progress=None) -> Pass2RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        # Load pass 1 accepted output.
        p1 = Pass1AcceptedOutput.model_validate(ctx.prior_accepted)

        # Load the median background plate.
        bg_path = ctx.paths.project_root / p1.median_background_path
        if not bg_path.exists():
            # Fallback: try pass1 raw dir.
            bg_path = ctx.paths.project_root / "passes" / "pass1" / "raw" / "median_background.png"
        bg = cv2.imread(str(bg_path))
        if bg is None:
            raise FileNotFoundError(f"Median background not found: {bg_path}")

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        progress.update(0.0, "detect_blobs", "Starting blob detection…")
        progress.check_cancelled()

        def _on_progress(frac: float, msg: str) -> None:
            progress.update(frac, "detect_blobs", msg)
            progress.check_cancelled()

        data = detect_blobs(
            video_path=ctx.video_path,
            bg=bg,
            in_time_s=p1.stable_bounds.in_time_s,
            out_time_s=p1.stable_bounds.out_time_s,
            fps=ctx.video_fps,
            threshold=DEFAULT_THRESHOLD,
            min_area=DEFAULT_MIN_AREA,
            max_area=DEFAULT_MAX_AREA,
            progress_callback=_on_progress,
            progress_start=0.02,
            progress_end=0.95,
        )

        # Write detections.json.
        progress.update(0.95, "write_outputs", "Writing detections…")
        dets_path = raw_dir / "detections.json"
        dets_path.write_text(json.dumps(data, separators=(",", ":")))

        # Write result.json (summary, without the frame data).
        result = Pass2RawResult(
            frame_count=data["frame_count"],
            detection_count=data["detection_count"],
            fps=data["fps"],
            bg_width=data["bg_width"],
            bg_height=data["bg_height"],
            threshold=data["threshold"],
            min_area=data["min_area"],
            max_area=data["max_area"],
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))

        progress.update(1.0, "write_outputs", "Pass 2 complete")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass2RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = [
            {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
            {"role": "raw", "type": "json", "path": str(raw_dir / "detections.json"),
             "name": "detections"},
        ]
        return [a for a in artifacts if Path(a["path"]).exists()]

    def validate_corrections(self, payload: dict) -> Pass2CorrectionPayload:
        return Pass2CorrectionPayload.model_validate(payload)

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass2RawResult,
        corrections: Pass2CorrectionPayload | None,
    ) -> Pass2AcceptedOutput:
        import shutil

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)

        # Copy raw files to accepted (no merging needed this milestone).
        raw_dir = ctx.paths.pass_raw_dir
        shutil.copy2(raw_dir / "result.json", accepted_dir / "result.json")
        shutil.copy2(raw_dir / "detections.json", accepted_dir / "detections.json")

        accepted = Pass2AcceptedOutput(
            frame_count=raw_result.frame_count,
            detection_count=raw_result.detection_count,
            fps=raw_result.fps,
            bg_width=raw_result.bg_width,
            bg_height=raw_result.bg_height,
            threshold=raw_result.threshold,
            min_area=raw_result.min_area,
            max_area=raw_result.max_area,
        )
        return accepted
