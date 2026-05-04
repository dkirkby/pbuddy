"""Smoke test for Pass 1 on the real test.mp4 video.

Run with:
    python3 -m pytest tests/integration/test_pass1_smoke.py -m slow -v -s

This test is excluded from the normal suite because it takes several minutes.
Pass 1 requires Pass 0 accepted output, so this test runs Pass 0 first.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# Path to the symlinked test video.
TEST_VIDEO = Path(__file__).parent.parent.parent / "test.mp4"


@pytest.mark.slow
def test_pass1_smoke(tmp_path):
    """Run Pass 0 then Pass 1 on test.mp4 and validate Pass 1 output."""
    if not TEST_VIDEO.exists():
        pytest.skip("test.mp4 not available")

    import shutil
    from pbva_core.config import Settings
    from pbva_core import paths as p
    from pbva_pipeline.base import LoggingProgress, PassContext, PassPaths
    from pbva_pipeline.pass0.run import Pass0
    from pbva_pipeline.pass1.run import Pass1

    settings = Settings(data_root=tmp_path / "data")
    project_id = "smoke-test-001"
    p.ensure_project_dirs(settings.data_root, project_id)

    dest = p.uploads_dir(settings.data_root, project_id) / "original.mp4"
    shutil.copy2(TEST_VIDEO, dest)

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(dest)],
        capture_output=True, text=True,
    )
    meta = json.loads(probe.stdout)
    vs = next(s for s in meta["streams"] if s["codec_type"] == "video")
    duration_s = float(meta["format"]["duration"])
    fps_str = vs["r_frame_rate"]
    num, den = fps_str.split("/")
    fps = float(num) / float(den)

    def make_ctx(pass_name):
        return PassContext(
            project_id=project_id,
            project_name="Smoke Test",
            video_path=dest,
            video_duration_s=duration_s,
            video_fps=fps,
            video_width=int(vs["width"]),
            video_height=int(vs["height"]),
            paths=PassPaths(
                project_root=p.project_root(settings.data_root, project_id),
                uploads_dir=p.uploads_dir(settings.data_root, project_id),
                derived_dir=p.derived_dir(settings.data_root, project_id),
                pass_raw_dir=p.pass_raw_dir(settings.data_root, project_id, pass_name),
                pass_corrections_dir=p.pass_corrections_dir(settings.data_root, project_id, pass_name),
                pass_accepted_dir=p.pass_accepted_dir(settings.data_root, project_id, pass_name),
            ),
            settings=settings,
            job_id=f"smoke-{pass_name}-001",
            progress=LoggingProgress(),
        )

    # ── Pass 0 ──────────────────────────────────────────────────────────────────
    print("\nRunning Pass 0…")
    ctx0 = make_ctx("pass0")
    pass0 = Pass0()
    pass0.validate_inputs(ctx0)
    raw0 = pass0.run(ctx0)
    pass0.build_accepted_output(ctx0, raw0, corrections=None)

    print(f"Pass 0: {raw0.median_count} medians, midpoint={raw0.midpoint_chunk}")

    # ── Pass 1 ──────────────────────────────────────────────────────────────────
    print("\nRunning Pass 1…")
    ctx1 = make_ctx("pass1")
    pass1 = Pass1()
    pass1.validate_inputs(ctx1)
    result = pass1.run(ctx1)

    # 1. result.json written.
    result_json = ctx1.paths.pass_raw_dir / "result.json"
    assert result_json.exists(), "result.json not written"

    # 2. Background dimensions are non-trivial.
    assert result.bg_width > 0 and result.bg_height > 0, (
        f"Invalid bg dimensions: {result.bg_width}×{result.bg_height}"
    )
    print(f"Background: {result.bg_width}×{result.bg_height}")

    # 3. Five court lines with sample points.
    assert len(result.court_lines) == 5, f"Expected 5 court lines, got {len(result.court_lines)}"
    for line in result.court_lines:
        assert len(line.points) > 0, f"Court line '{line.name}' has no sample points"
    line_names = [l.name for l in result.court_lines]
    print(f"Court lines: {line_names}")

    # 4. Chunk profiles cover all pass0 medians.
    assert len(result.chunks) == raw0.median_count, (
        f"Expected {raw0.median_count} chunks, got {len(result.chunks)}"
    )
    print(f"Chunks: {len(result.chunks)}")

    print(f"\nAll acceptance criteria passed for test.mp4.")
    print(f"Outputs in: {ctx1.paths.pass_raw_dir}")
