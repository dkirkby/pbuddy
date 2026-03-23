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
        bg_path = ctx.paths.project_root / "passes" / "pass1" / "raw" / "median_background.png"
        if not bg_path.exists():
            raise FileNotFoundError(f"Median background not found: {bg_path}")

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

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(ctx.paths.pass_raw_dir / "result.json", accepted_dir / "result.json")

        annotations = corrections.annotations if corrections else {}

        # Write annotations to accepted dir.
        ann_data = {k: {"x": v.x, "y": v.y} for k, v in annotations.items()}
        (accepted_dir / "annotations.json").write_text(
            json.dumps({"annotations": ann_data}, indent=2)
        )

        return Pass2AcceptedOutput(
            fps=raw_result.fps,
            bg_width=raw_result.bg_width,
            bg_height=raw_result.bg_height,
            annotation_count=len(annotations),
        )
