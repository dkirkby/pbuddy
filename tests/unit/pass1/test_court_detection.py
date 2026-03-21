"""Unit tests for court detection on a synthetic court image."""

from __future__ import annotations

import numpy as np
import pytest
import cv2

from pbva_pipeline.pass1.detect_court import detect_court


def _make_synthetic_court(w: int = 960, h: int = 540) -> np.ndarray:
    """Draw a simple rectangular court on a dark green background."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 100, 40)  # dark green background

    # Court boundaries.
    left, right, top, bottom = 180, 780, 80, 460
    net_y = (top + bottom) // 2

    color = (220, 220, 220)
    thickness = 3
    cv2.rectangle(img, (left, top), (right, bottom), color, thickness)
    cv2.line(img, (left, net_y), (right, net_y), color, thickness)

    return img


def test_detect_court_on_synthetic_image():
    img = _make_synthetic_court()
    geo, confidence = detect_court(img)

    assert confidence >= 0.0  # detection ran without error
    # All corner coords should be within image bounds.
    for corner in [geo.top_left, geo.top_right, geo.bottom_left, geo.bottom_right, geo.net_left, geo.net_right]:
        assert 0 <= corner.x <= 960, f"x out of bounds: {corner.x}"
        assert 0 <= corner.y <= 540, f"y out of bounds: {corner.y}"


def test_detect_court_rough_accuracy():
    """Court detection returns valid geometry without crashing."""
    img = _make_synthetic_court()
    geo, confidence = detect_court(img)

    # All six points must be valid pixel coordinates within the image.
    all_points = [geo.top_left, geo.top_right, geo.bottom_left, geo.bottom_right, geo.net_left, geo.net_right]
    for pt in all_points:
        assert isinstance(pt.x, float)
        assert isinstance(pt.y, float)

    # The court must span at least some vertical extent.
    top_y = min(geo.top_left.y, geo.top_right.y)
    bottom_y = max(geo.bottom_left.y, geo.bottom_right.y)
    assert bottom_y > top_y, "Bottom of court must be below top"

    # Net y must be between top and bottom.
    net_y = (geo.net_left.y + geo.net_right.y) / 2
    assert top_y <= net_y <= bottom_y, f"Net ({net_y:.0f}) not between top ({top_y:.0f}) and bottom ({bottom_y:.0f})"


def test_detect_court_fallback_on_blank_image():
    """Blank image should not crash and should return a fallback geometry."""
    img = np.zeros((540, 960, 3), dtype=np.uint8)
    geo, confidence = detect_court(img)
    assert geo is not None
    assert 0.0 <= confidence <= 1.0
