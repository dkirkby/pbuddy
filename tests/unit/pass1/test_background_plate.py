"""Unit tests for background plate median computation."""

from __future__ import annotations

import numpy as np
import pytest


def _compute_median_plate(frames: list[np.ndarray]) -> np.ndarray:
    """Core median logic (without video I/O)."""
    stack = np.stack(frames, axis=0)
    return np.median(stack, axis=0).astype(np.uint8)


def test_median_erases_moving_object():
    """A pixel that is bright in fewer than half the frames should be dark in the median."""
    rng = np.random.default_rng(0)
    h, w = 10, 10
    # 10 dark frames.
    frames = [np.full((h, w, 3), 30, dtype=np.uint8) for _ in range(10)]
    # 3 bright frames (simulating a moving object present in minority of frames).
    for _ in range(3):
        bright = np.full((h, w, 3), 200, dtype=np.uint8)
        frames.append(bright)

    median = _compute_median_plate(frames)
    # Median of [30]*10 + [200]*3 = 30.
    assert np.all(median <= 35), f"Expected ~30, got {median.mean():.1f}"


def test_median_correct_value():
    h, w = 5, 5
    frames = [np.full((h, w, 3), v, dtype=np.uint8) for v in [10, 20, 30, 40, 50]]
    median = _compute_median_plate(frames)
    assert np.all(median == 30)


def test_median_shape_preserved():
    h, w = 540, 960
    frames = [np.random.randint(0, 255, (h, w, 3), dtype=np.uint8) for _ in range(5)]
    median = _compute_median_plate(frames)
    assert median.shape == (h, w, 3)
    assert median.dtype == np.uint8
