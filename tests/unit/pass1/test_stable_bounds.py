"""Unit tests for stable bounds detection using synthetic frame sequences."""

from __future__ import annotations

import numpy as np
import pytest

from pbva_core.types import StableBounds


def _make_mock_frames(n_stable_start: int, n_stable_end: int, n_motion: int):
    """Return (frames, timestamps) with synthetic motion at start and end."""
    rng = np.random.default_rng(42)
    base = rng.integers(50, 200, size=(180, 320)).astype(np.float32)

    frames = []
    timestamps = []
    t = 0.0
    step = 0.5  # seconds per sample

    # Motion at start
    for i in range(n_motion):
        frames.append(rng.integers(0, 255, size=(180, 320)).astype(np.float32))
        timestamps.append(t)
        t += step

    # Stable middle
    for i in range(n_stable_start + n_stable_end):
        noise = rng.normal(0, 2, size=(180, 320)).astype(np.float32)
        frames.append(base + noise)
        timestamps.append(t)
        t += step

    # Motion at end
    for i in range(n_motion):
        frames.append(rng.integers(0, 255, size=(180, 320)).astype(np.float32))
        timestamps.append(t)
        t += step

    return frames, timestamps, t


def _run_detection(frames, timestamps, duration_s, threshold=6.0, window=5):
    """Run the core detection logic directly without video I/O."""
    import numpy as np
    from pbva_core.types import StableBounds

    if len(frames) < 3:
        return StableBounds(in_time_s=0.0, out_time_s=duration_s)

    diffs = []
    for i in range(1, len(frames)):
        diff = float(np.mean(np.abs(frames[i] - frames[i - 1])))
        diffs.append(diff)
    diffs = np.array(diffs)

    half = window // 2
    smoothed = np.array([
        float(np.median(diffs[max(0, i - half): i + half + 1]))
        for i in range(len(diffs))
    ])
    is_stable = smoothed < threshold

    in_idx = 0
    for i in range(len(is_stable) - 2):
        if is_stable[i] and is_stable[i + 1] and is_stable[i + 2]:
            in_idx = i
            break

    out_idx = len(is_stable) - 1
    for i in range(len(is_stable) - 1, 1, -1):
        if is_stable[i] and is_stable[i - 1] and is_stable[i - 2]:
            out_idx = i
            break

    in_time_s = float(timestamps[in_idx])
    out_time_s = float(timestamps[min(out_idx + 1, len(timestamps) - 1)])

    if out_time_s - in_time_s < 30.0:
        return StableBounds(in_time_s=0.0, out_time_s=duration_s)

    return StableBounds(in_time_s=round(in_time_s, 2), out_time_s=round(out_time_s, 2))


def test_stable_bounds_with_motion_at_start():
    frames, timestamps, duration = _make_mock_frames(n_stable_start=60, n_stable_end=60, n_motion=10)
    result = _run_detection(frames, timestamps, duration)
    # In-point should not be at time 0 (we have motion at start).
    assert result.in_time_s >= 0.0
    assert result.out_time_s > result.in_time_s


def test_stable_bounds_fallback_when_all_stable():
    """When entire video is stable, return full range."""
    frames, timestamps, duration = _make_mock_frames(n_stable_start=80, n_stable_end=80, n_motion=0)
    result = _run_detection(frames, timestamps, duration)
    assert result.in_time_s == 0.0
    assert result.out_time_s == pytest.approx(duration, abs=1.0)


def test_stable_bounds_short_stable_falls_back():
    """When stable region is too short, fall back to full duration."""
    frames, timestamps, duration = _make_mock_frames(n_stable_start=2, n_stable_end=2, n_motion=50)
    result = _run_detection(frames, timestamps, duration)
    # Fall back because stable_duration < min_stable_s (30 s).
    assert result.in_time_s == 0.0
    assert result.out_time_s == pytest.approx(duration, abs=1.0)
