"""Pass 2 orchestration — ball annotation tool setup."""

from __future__ import annotations

import json
from pathlib import Path

from pbva_core.types import (
    Pass1AcceptedOutput,
    Pass2AcceptedOutput,
    Pass2CorrectionPayload,
    Pass2RawResult,
)
from pbva_pipeline.base import PassContext


class Pass2:
    name = "pass2"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")
        if not ctx.prior_accepted:
            raise ValueError("Pass 1 accepted output is required for Pass 2")
        medians_dir = ctx.paths.project_root / "passes" / "pass0" / "raw" / "medians"
        if not medians_dir.exists() or not any(medians_dir.glob("median_*.png")):
            raise FileNotFoundError("Pass 0 median images not found; run Pass 0 first")

    def run(self, ctx: PassContext, progress=None) -> Pass2RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.1, "setup", "Reading video metadata…")
        p1 = Pass1AcceptedOutput.model_validate(ctx.prior_accepted)

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        result = Pass2RawResult(
            fps=ctx.video_fps,
            bg_width=p1.bg_width,
            bg_height=p1.bg_height,
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))

        progress.update(1.0, "setup", "Pass 2 ready for annotation")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass2RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        result_path = raw_dir / "result.json"
        return [{"role": "raw", "type": "json", "path": str(result_path)}] if result_path.exists() else []

    def validate_corrections(self, payload: dict) -> Pass2CorrectionPayload:
        return Pass2CorrectionPayload.model_validate(payload)

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass2RawResult,
        corrections: Pass2CorrectionPayload | None,
    ) -> Pass2AcceptedOutput:
        import shutil
        import cv2

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)

        annotations = corrections.annotations if corrections else {}

        radii = [v.radius for v in annotations.values() if v.radius > 0]
        min_r = round(min(radii)) if radii else 4
        max_r = round(max(radii)) if radii else 16

        ann_data = {k: {"x": v.x, "y": v.y, "radius": v.radius} for k, v in annotations.items()}
        (accepted_dir / "annotations.json").write_text(
            json.dumps({"annotations": ann_data}, indent=2)
        )

        # Copy raw patches and compute background-subtracted patches.
        raw_src = ctx.paths.pass_corrections_dir / "patches" / "raw"
        if raw_src.exists() and any(raw_src.glob("*.png")):
            raw_dst = accepted_dir / "patches" / "raw"
            bg_sub_dst = accepted_dir / "patches" / "bg_sub"
            raw_dst.mkdir(parents=True, exist_ok=True)
            bg_sub_dst.mkdir(parents=True, exist_ok=True)

            pass0_raw_path = ctx.paths.project_root / "passes" / "pass0" / "raw" / "result.json"
            medians_dir = ctx.paths.project_root / "passes" / "pass0" / "raw" / "medians"
            median_count = 1
            if pass0_raw_path.exists():
                median_count = json.loads(pass0_raw_path.read_text()).get("median_count", 1) or 1
            total_frames = max(ctx.video_fps * ctx.video_duration_s, 1.0)
            bg_cache: dict[int, object] = {}

            def _get_bg(frame_key: str):
                ocv = int(frame_key) - 1
                ci = min(int(ocv * median_count / total_frames), median_count - 1)
                if ci not in bg_cache:
                    bg_cache[ci] = cv2.imread(str(medians_dir / f"median_{ci:03d}.png"))
                return bg_cache[ci]

            for src_png in sorted(raw_src.glob("*.png")):
                shutil.copy2(src_png, raw_dst / src_png.name)

                raw_patch = cv2.imread(str(src_png))
                if raw_patch is None:
                    continue
                h, w = raw_patch.shape[:2]
                frame_str = str(int(src_png.stem))
                if frame_str not in ann_data:
                    continue
                bg_plate = _get_bg(frame_str)
                if bg_plate is None:
                    continue
                cx = int(round(ann_data[frame_str]["x"]))
                cy = int(round(ann_data[frame_str]["y"]))
                x1, y1 = cx - w // 2, cy - h // 2
                x2, y2 = x1 + w, y1 + h
                bg_crop = bg_plate[max(0, y1):y2, max(0, x1):x2]
                if bg_crop.shape == raw_patch.shape:
                    cv2.imwrite(str(bg_sub_dst / src_png.name), cv2.absdiff(raw_patch, bg_crop))

        accepted = Pass2AcceptedOutput(
            fps=raw_result.fps,
            bg_width=raw_result.bg_width,
            bg_height=raw_result.bg_height,
            annotation_count=len(annotations),
            min_ball_radius=min_r,
            max_ball_radius=max_r,
        )
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
