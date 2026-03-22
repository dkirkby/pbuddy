"""Unit tests for Pass 2 blob detection logic."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from pbva_pipeline.pass2.detect_blobs import (
    DEFAULT_MAX_AREA,
    DEFAULT_MIN_AREA,
    DEFAULT_THRESHOLD,
    _process_frame,
)


def _kernels():
    open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return open_k, close_k


def test_no_detections_identical_frame():
    """An identical frame and background yields no detections."""
    bg = np.full((540, 960, 3), [10, 100, 30], dtype=np.uint8)
    frame = bg.copy()
    open_k, close_k = _kernels()
    dets = _process_frame(frame, bg, DEFAULT_THRESHOLD, DEFAULT_MIN_AREA, DEFAULT_MAX_AREA, open_k, close_k)
    assert dets == []


def test_circle_blob_detected():
    """A white circle on a dark background is detected as a single blob."""
    bg = np.zeros((540, 960, 3), dtype=np.uint8)
    frame = bg.copy()
    cx_true, cy_true, radius = 480, 270, 22
    cv2.circle(frame, (cx_true, cy_true), radius, (200, 200, 200), -1)

    open_k, close_k = _kernels()
    dets = _process_frame(frame, bg, threshold=20, min_area=300, max_area=160_000, open_kernel=open_k, close_kernel=close_k)

    assert len(dets) == 1
    det = dets[0]
    # Centroid should be within 5 pixels of truth.
    assert abs(det["cx"] - cx_true) < 5, f"cx off: {det['cx']}"
    assert abs(det["cy"] - cy_true) < 5, f"cy off: {det['cy']}"
    # Area should be roughly π·r².
    expected_area = math.pi * radius ** 2
    assert 0.5 * expected_area < det["area"] < 2.0 * expected_area
    # Blob should be roughly circular (b/a close to 1).
    assert det["b"] / det["a"] > 0.7


def test_small_blob_filtered():
    """A blob below min_area is not returned."""
    bg = np.zeros((540, 960, 3), dtype=np.uint8)
    frame = bg.copy()
    cv2.circle(frame, (480, 270), 3, (200, 200, 200), -1)  # tiny circle

    open_k, close_k = _kernels()
    dets = _process_frame(frame, bg, threshold=20, min_area=300, max_area=160_000, open_kernel=open_k, close_kernel=close_k)
    assert dets == []


def test_large_blob_filtered():
    """A blob above max_area is not returned."""
    bg = np.zeros((540, 960, 3), dtype=np.uint8)
    frame = bg.copy()
    # Fill a large rectangle.
    cv2.rectangle(frame, (100, 100), (700, 400), (200, 200, 200), -1)

    open_k, close_k = _kernels()
    dets = _process_frame(frame, bg, threshold=20, min_area=300, max_area=160_000, open_kernel=open_k, close_kernel=close_k)
    assert dets == []


def test_frame_resized_to_bg():
    """Frames of different size are resized before processing."""
    bg = np.zeros((540, 960, 3), dtype=np.uint8)
    # Frame at double resolution with a circle.
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cv2.circle(frame, (960, 540), 44, (200, 200, 200), -1)  # same logical position

    open_k, close_k = _kernels()
    dets = _process_frame(frame, bg, threshold=20, min_area=300, max_area=160_000, open_kernel=open_k, close_kernel=close_k)
    assert len(dets) == 1
    # Centroid should be near (480, 270) after downscale.
    assert abs(dets[0]["cx"] - 480) < 10
    assert abs(dets[0]["cy"] - 270) < 10


def test_temporal_diff_catches_ball_in_background():
    """Ball coinciding with the median background is caught via temporal diff."""
    # Background already contains a ball at position A.
    bg = np.zeros((540, 960, 3), dtype=np.uint8)
    cv2.circle(bg, (480, 270), 22, (200, 200, 200), -1)

    # Current frame: ball still at position A (identical to background there).
    frame = bg.copy()

    # Previous frame: ball was 50px to the left.
    prev_frame = np.zeros((540, 960, 3), dtype=np.uint8)
    cv2.circle(prev_frame, (430, 270), 22, (200, 200, 200), -1)

    open_k, close_k = _kernels()

    # Without temporal diff: background sub sees no diff → no detection.
    dets_no_temp = _process_frame(
        frame, bg, threshold=20, min_area=50, max_area=160_000,
        open_kernel=open_k, close_kernel=close_k, prev_frame=None,
    )
    assert dets_no_temp == [], f"Expected no detections without temporal diff, got {dets_no_temp}"

    # With temporal diff: frame vs prev_frame shows both positions → detects ball.
    dets_with_temp = _process_frame(
        frame, bg, threshold=20, min_area=50, max_area=160_000,
        open_kernel=open_k, close_kernel=close_k, prev_frame=prev_frame,
    )
    assert len(dets_with_temp) >= 1, "Expected at least one detection with temporal diff"


def test_multiple_blobs():
    """Two separate blobs are detected independently."""
    bg = np.zeros((540, 960, 3), dtype=np.uint8)
    frame = bg.copy()
    cv2.circle(frame, (200, 200), 22, (200, 200, 200), -1)
    cv2.circle(frame, (700, 350), 22, (200, 200, 200), -1)

    open_k, close_k = _kernels()
    dets = _process_frame(frame, bg, threshold=20, min_area=300, max_area=160_000, open_kernel=open_k, close_kernel=close_k)
    assert len(dets) == 2
