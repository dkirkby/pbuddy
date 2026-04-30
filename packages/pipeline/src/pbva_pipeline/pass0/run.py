"""Pass 0 orchestration — chunked median images and camera model specification."""

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

BG_SUBSAMPLE = 10   # use every Nth frame within each chunk for the median


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

        progress.update(0.0, "setup", "Opening video…")
        progress.check_cancelled()

        cap = cv2.VideoCapture(str(ctx.video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {ctx.video_path}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or ctx.video_fps or 30.0
        chunk_size = max(1, round(4.0 * fps))   # 4-second chunks

        n_chunks = max(1, (total_frames + chunk_size - 1) // chunk_size)
        midpoint_chunk = n_chunks // 2

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        medians_dir = raw_dir / "medians"
        medians_dir.mkdir(parents=True, exist_ok=True)

        bg_h = bg_w = 0
        saved_chunks = 0

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        for chunk_idx in range(n_chunks):
            progress.check_cancelled()
            progress.update(
                chunk_idx / n_chunks,
                "compute_median",
                f"Chunk {chunk_idx + 1}/{n_chunks}…",
            )

            chunk_start = chunk_idx * chunk_size
            chunk_end = min(chunk_start + chunk_size - 1, total_frames - 1)

            # Read chunk sequentially; keep every BG_SUBSAMPLE-th frame.
            samples: list[np.ndarray] = []
            for local_i in range(chunk_end - chunk_start + 1):
                ok, frm = cap.read()
                if ok and local_i % BG_SUBSAMPLE == 0:
                    samples.append(frm)

            if not samples:
                continue

            median = np.median(np.stack(samples, axis=0), axis=0).astype(np.uint8)
            if bg_h == 0:
                bg_h, bg_w = median.shape[:2]

            cv2.imwrite(str(medians_dir / f"median_{chunk_idx:03d}.png"), median)
            saved_chunks += 1

        cap.release()

        if bg_h == 0:
            raise RuntimeError("No frames could be read from video")

        result = Pass0RawResult(
            bg_width=bg_w,
            bg_height=bg_h,
            median_count=saved_chunks,
            midpoint_chunk=midpoint_chunk,
            video_fps=fps,
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
        progress.update(1.0, "write_outputs", f"Pass 0 complete — {saved_chunks} median images")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass0RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        outputs = [{"role": "raw", "type": "json", "path": str(raw_dir / "result.json")}]
        medians_dir = raw_dir / "medians"
        if medians_dir.exists():
            for png in sorted(medians_dir.glob("median_*.png")):
                outputs.append({"role": "raw", "type": "png", "path": str(png)})
        return outputs

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
