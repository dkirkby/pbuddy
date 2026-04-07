"""Smoke test for Pass 1 on the real test.mp4 video.

Run with:
    python3 -m pytest tests/integration/test_pass1_smoke.py -m slow -v -s

This test is excluded from the normal suite because it takes several minutes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Path to the symlinked test video.
TEST_VIDEO = Path(__file__).parent.parent.parent / "test.mp4"


@pytest.mark.slow
def test_pass1_smoke(tmp_path):
    """Run Pass 1 directly on test.mp4 and validate the output."""
    if not TEST_VIDEO.exists():
        pytest.skip("test.mp4 not available")

    import shutil
    from pbva_core.config import Settings
    from pbva_core import paths as p
    from pbva_pipeline.base import LoggingProgress, PassContext, PassPaths
    from pbva_pipeline.pass1.run import Pass1

    settings = Settings(data_root=tmp_path / "data")
    project_id = "smoke-test-001"
    p.ensure_project_dirs(settings.data_root, project_id)

    # Copy test video into uploads dir.
    dest = p.uploads_dir(settings.data_root, project_id) / "original.mp4"
    shutil.copy2(TEST_VIDEO, dest)

    # Probe video.
    import json, subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(dest)],
        capture_output=True, text=True
    )
    meta = json.loads(probe.stdout)
    vs = next(s for s in meta["streams"] if s["codec_type"] == "video")
    duration_s = float(meta["format"]["duration"])
    fps_str = vs["r_frame_rate"]
    num, den = fps_str.split("/")
    fps = float(num) / float(den)

    pass_paths = PassPaths(
        project_root=p.project_root(settings.data_root, project_id),
        uploads_dir=p.uploads_dir(settings.data_root, project_id),
        derived_dir=p.derived_dir(settings.data_root, project_id),
        pass_raw_dir=p.pass_raw_dir(settings.data_root, project_id, "pass1"),
        pass_corrections_dir=p.pass_corrections_dir(settings.data_root, project_id, "pass1"),
        pass_accepted_dir=p.pass_accepted_dir(settings.data_root, project_id, "pass1"),
    )

    ctx = PassContext(
        project_id=project_id,
        project_name="Smoke Test",
        video_path=dest,
        video_duration_s=duration_s,
        video_fps=fps,
        video_width=int(vs["width"]),
        video_height=int(vs["height"]),
        paths=pass_paths,
        settings=settings,
        job_id="smoke-job-001",
        progress=LoggingProgress(),
    )

    pass1 = Pass1()
    pass1.validate_inputs(ctx)
    result = pass1.run(ctx)

    # ── Acceptance criteria ──

    # 1. Stable bounds: in-point < 30s, out-point > 10 min from start.
    assert result.stable_bounds.in_time_s < 30, (
        f"Expected in_time_s < 30, got {result.stable_bounds.in_time_s}"
    )
    assert result.stable_bounds.out_time_s > 600, (
        f"Expected out_time_s > 600, got {result.stable_bounds.out_time_s}"
    )
    print(f"\nStable bounds: {result.stable_bounds.in_time_s:.1f}s – {result.stable_bounds.out_time_s:.1f}s")

    # 2. Median background image(s) exist and have correct shape.
    import cv2
    assert result.median_background_paths, "No median_background_paths in result"
    for rel_path in result.median_background_paths:
        bg_path = pass_paths.project_root / rel_path
        assert bg_path.exists(), f"{rel_path} not written"
        bg = cv2.imread(str(bg_path))
        assert bg is not None, f"Could not read {rel_path}"
        assert bg.shape == (540, 960, 3), f"Expected (540, 960, 3), got {bg.shape}"
        print(f"Background plate: {bg.shape}, written to {bg_path}")

    # 3. Court overlay exists.
    overlay_path = pass_paths.pass_raw_dir / "court_overlay.png"
    assert overlay_path.exists(), "court_overlay.png not written"

    # 4. Court geometry is non-trivial (corners span some area).
    g = result.court_geometry
    x_span = max(g.top_right.x, g.bottom_right.x) - min(g.top_left.x, g.bottom_left.x)
    y_span = max(g.bottom_left.y, g.bottom_right.y) - min(g.top_left.y, g.top_right.y)
    assert x_span > 50, f"Court x-span too small: {x_span}"
    assert y_span > 50, f"Court y-span too small: {y_span}"
    print(f"Court geometry: x_span={x_span:.0f}px, y_span={y_span:.0f}px, confidence={result.confidence}")

    # 5. result.json written.
    result_json = pass_paths.pass_raw_dir / "result.json"
    assert result_json.exists(), "result.json not written"

    print(f"\nAll acceptance criteria passed for test.mp4.")
    print(f"Outputs in: {pass_paths.pass_raw_dir}")
