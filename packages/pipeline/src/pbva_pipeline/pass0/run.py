"""Pass 0 orchestration — median image and camera model specification."""

from __future__ import annotations

import cv2  # type: ignore
import numpy as np

from pbva_core.types import (
    CourtCorner,
    CourtGeometry,
    Pass0AcceptedOutput,
    Pass0CorrectionPayload,
    Pass0RawResult,
)
from pbva_pipeline.base import PassContext


def _default_court(bg_w: int, bg_h: int) -> CourtGeometry:
    return CourtGeometry(
        top_left=CourtCorner(x=0.35 * bg_w, y=0.30 * bg_h),
        top_right=CourtCorner(x=0.65 * bg_w, y=0.30 * bg_h),
        bottom_left=CourtCorner(x=0.05 * bg_w, y=0.90 * bg_h),
        bottom_right=CourtCorner(x=0.95 * bg_w, y=0.90 * bg_h),
    )


class Pass0:
    name = "pass0"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")

    def run(self, ctx: PassContext, progress=None) -> Pass0RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.0, "sample_frames", "Sampling frames for median…")
        progress.check_cancelled()

        cap = cv2.VideoCapture(str(ctx.video_path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        n_frames = 30
        stride = 15
        midpoint = total_frames // 2
        half_span = stride * (n_frames - 1) // 2
        start = max(0, midpoint - half_span)
        frame_indices = [min(start + i * stride, total_frames - 1) for i in range(n_frames)]

        frames: list[np.ndarray] = []
        for i, fi in enumerate(frame_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
            frac = (i + 1) / n_frames * 0.9
            progress.update(frac, "sample_frames", f"Reading frame {i + 1}/{n_frames}…")
            progress.check_cancelled()
        cap.release()

        if not frames:
            raise RuntimeError("No frames could be read from video")

        progress.update(0.90, "compute_median", "Computing pixel-wise median…")
        progress.check_cancelled()
        stack = np.stack(frames, axis=0)
        median = np.median(stack, axis=0).astype(np.uint8)

        bg_h, bg_w = median.shape[:2]
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(raw_dir / "median.png"), median)

        result = Pass0RawResult(bg_width=bg_w, bg_height=bg_h)
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
        progress.update(1.0, "write_outputs", "Pass 0 complete")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass0RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        return [
            {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
            {"role": "raw", "type": "png",  "path": str(raw_dir / "median.png")},
        ]

    def validate_corrections(self, payload: dict) -> Pass0CorrectionPayload:
        return Pass0CorrectionPayload.model_validate(payload)

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass0RawResult,
        corrections: Pass0CorrectionPayload | None,
    ) -> Pass0AcceptedOutput:
        court_geo = (
            corrections.court_geometry
            if corrections and corrections.court_geometry
            else _default_court(raw_result.bg_width, raw_result.bg_height)
        )
        k1 = corrections.k1 if corrections and corrections.k1 is not None else 0.0

        accepted = Pass0AcceptedOutput(
            court_geometry=court_geo,
            k1=k1,
            bg_width=raw_result.bg_width,
            bg_height=raw_result.bg_height,
        )
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
