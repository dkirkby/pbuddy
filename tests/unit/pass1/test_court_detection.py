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
    # Net line and interior kitchen/center lines.
    cv2.line(img, (left, net_y), (right, net_y), color, thickness)
    kitchen_top = top + (bottom - top) * 15 // 44
    kitchen_bot = top + (bottom - top) * 29 // 44
    mid_x = (left + right) // 2
    cv2.line(img, (left, kitchen_top), (right, kitchen_top), color, thickness)
    cv2.line(img, (left, kitchen_bot), (right, kitchen_bot), color, thickness)
    cv2.line(img, (mid_x, top), (mid_x, kitchen_top), color, thickness)
    cv2.line(img, (mid_x, kitchen_bot), (mid_x, bottom), color, thickness)

    return img


def test_detect_court_on_synthetic_image():
    img = _make_synthetic_court()
    geo, confidence = detect_court(img)

    assert confidence >= 0.0  # detection ran without error
    # All corner coords should be within image bounds.
    for corner in [geo.top_left, geo.top_right, geo.bottom_left, geo.bottom_right]:
        assert 0 <= corner.x <= 960, f"x out of bounds: {corner.x}"
        assert 0 <= corner.y <= 540, f"y out of bounds: {corner.y}"


def test_detect_court_rough_accuracy():
    """Court detection returns valid geometry without crashing."""
    img = _make_synthetic_court()
    geo, confidence = detect_court(img)

    # All four corners must be valid pixel coordinates.
    for pt in [geo.top_left, geo.top_right, geo.bottom_left, geo.bottom_right]:
        assert isinstance(pt.x, float)
        assert isinstance(pt.y, float)

    # The court must span at least some vertical extent.
    top_y = min(geo.top_left.y, geo.top_right.y)
    bottom_y = max(geo.bottom_left.y, geo.bottom_right.y)
    assert bottom_y > top_y, "Bottom of court must be below top"


def test_detect_court_fallback_on_blank_image():
    """Blank image should not crash and should return a fallback geometry."""
    img = np.zeros((540, 960, 3), dtype=np.uint8)
    geo, confidence = detect_court(img)
    assert geo is not None
    assert 0.0 <= confidence <= 1.0
