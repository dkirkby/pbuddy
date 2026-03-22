"""Integration test: Pass 2 lifecycle on test.mp4.

Requires Pass 1 to have been run first (or can be run as a combined smoke test).

Run with:
    uv run pytest tests/integration/test_pass2_lifecycle.py -m slow -v -s
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

TEST_VIDEO = Path(__file__).parent.parent.parent / "test.mp4"


@pytest.mark.slow
def test_pass2_lifecycle(tmp_path):
    """Run Pass 1 then Pass 2 on test.mp4 and validate Pass 2 artifacts."""
    if not TEST_VIDEO.exists():
        pytest.skip("test.mp4 not available")

    import subprocess

    from pbva_core.config import Settings
    from pbva_core import paths as p
    from pbva_core.types import Pass1AcceptedOutput, Pass1CorrectionPayload, Pass1RawResult
    from pbva_pipeline.base import LoggingProgress, PassContext, PassPaths
    from pbva_pipeline.pass1.run import Pass1
    from pbva_pipeline.pass2.run import Pass2

    settings = Settings(data_root=tmp_path / "data")
    project_id = "pass2-smoke-001"
    p.ensure_project_dirs(settings.data_root, project_id)

    dest = p.uploads_dir(settings.data_root, project_id) / "original.mp4"
    shutil.copy2(TEST_VIDEO, dest)

    # Probe video.
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(dest)],
        capture_output=True, text=True,
    )
    meta = json.loads(probe.stdout)
    vs = next(s for s in meta["streams"] if s["codec_type"] == "video")
    duration_s = float(meta["format"]["duration"])
    num, den = vs["r_frame_rate"].split("/")
    fps = float(num) / float(den)

    def make_ctx(pass_name, prior_accepted=None):
        return PassContext(
            project_id=project_id,
            project_name="Pass2 Lifecycle Test",
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
            job_id=f"smoke-job-{pass_name}",
            progress=LoggingProgress(),
            prior_accepted=prior_accepted or {},
        )

    # ── Run Pass 1 and accept it ──
    print("\n[Pass 1] Running…")
    ctx1 = make_ctx("pass1")
    p1 = Pass1()
    p1.validate_inputs(ctx1)
    raw1 = p1.run(ctx1)

    # Build accepted output (simulate accept with no corrections).
    accepted1 = p1.build_accepted_output(ctx1, raw1, None)
    assert (ctx1.paths.pass_accepted_dir / "result.json").exists()

    # ── Run Pass 2 ──
    print("\n[Pass 2] Running…")
    accepted1_dict = json.loads((ctx1.paths.pass_accepted_dir / "result.json").read_text())
    ctx2 = make_ctx("pass2", prior_accepted=accepted1_dict)

    p2 = Pass2()
    p2.validate_inputs(ctx2)
    raw2 = p2.run(ctx2)

    # ── Validate raw artifacts ──

    raw_dir = ctx2.paths.pass_raw_dir

    # result.json exists and has expected fields.
    result_json = raw_dir / "result.json"
    assert result_json.exists(), "result.json not written"
    result = json.loads(result_json.read_text())
    assert result["frame_count"] > 0, f"frame_count should be > 0, got {result['frame_count']}"
    assert result["fps"] > 0
    assert result["bg_width"] > 0 and result["bg_height"] > 0
    print(f"  frame_count={result['frame_count']}, detection_count={result['detection_count']}")

    # detections.json exists and is valid.
    dets_json = raw_dir / "detections.json"
    assert dets_json.exists(), "detections.json not written"
    dets = json.loads(dets_json.read_text())
    assert "frames" in dets
    assert "fps" in dets
    assert dets["bg_width"] > 0 and dets["bg_height"] > 0

    # At least some detections should exist.
    assert dets["detection_count"] > 0, (
        "Expected some detections, but detection_count == 0. "
        "Check threshold and video content."
    )

    # Validate a sample detection record.
    first_frame_dets = next(iter(dets["frames"].values()))
    assert isinstance(first_frame_dets, list) and len(first_frame_dets) > 0
    det = first_frame_dets[0]
    for field in ("cx", "cy", "a", "b", "angle", "area", "bbox_x", "bbox_y", "bbox_w", "bbox_h"):
        assert field in det, f"Missing field '{field}' in detection"

    print(f"\nPass 2 integration test passed.")
    print(f"Outputs in: {raw_dir}")
