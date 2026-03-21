"""Detect the primary pickleball court in a median background image.

Uses Canny edges and Hough line transform to find court lines, then fits
a perspective quadrilateral to determine the four court corners and two
net endpoints.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from pbva_core.types import CourtCorner, CourtGeometry


def _line_intersection(l1, l2):
    """Return (x, y) intersection of two lines in (rho, theta) form, or None."""
    rho1, theta1 = l1
    rho2, theta2 = l2
    A = np.array([
        [np.cos(theta1), np.sin(theta1)],
        [np.cos(theta2), np.sin(theta2)],
    ])
    b = np.array([rho1, rho2])
    det = np.linalg.det(A)
    if abs(det) < 1e-6:
        return None
    pt = np.linalg.solve(A, b)
    return float(pt[0]), float(pt[1])


def _cluster_lines(lines, angle_thresh=0.15, rho_thresh=40):
    """Cluster Hough lines by similar angle and rho, return representative lines."""
    if lines is None or len(lines) == 0:
        return []
    clusters: list[list] = []
    for line in lines:
        rho, theta = float(line[0]), float(line[1])
        matched = False
        for cluster in clusters:
            crho, ctheta = cluster[0]
            if abs(theta - ctheta) < angle_thresh and abs(rho - crho) < rho_thresh:
                cluster.append((rho, theta))
                matched = True
                break
        if not matched:
            clusters.append([(rho, theta)])
    # Return the median representative of each cluster.
    result = []
    for cluster in clusters:
        rhos = [c[0] for c in cluster]
        thetas = [c[1] for c in cluster]
        result.append((float(np.median(rhos)), float(np.median(thetas))))
    return result


def detect_court(
    median_bg: np.ndarray,
    output_path: Path | None = None,
) -> tuple[CourtGeometry, float]:
    """Detect the primary court in the median background image.

    Returns:
        (CourtGeometry, confidence): geometry in pixel coords of the working
        resolution image, and a confidence score in [0, 1].
    """
    h, w = median_bg.shape[:2]
    gray = cv2.cvtColor(median_bg, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150, apertureSize=3)

    lines_raw = cv2.HoughLines(edges, rho=1, theta=np.pi / 180, threshold=80)
    if lines_raw is None:
        geometry = _fallback_geometry(w, h)
        return geometry, 0.0

    lines = [(float(l[0][0]), float(l[0][1])) for l in lines_raw]

    # Separate into near-horizontal (theta ~ pi/2) and near-vertical (theta ~ 0 or pi).
    h_lines = [(r, t) for r, t in lines if abs(t - np.pi / 2) < 0.4]
    v_lines = [(r, t) for r, t in lines if t < 0.4 or t > np.pi - 0.4]

    h_clusters = _cluster_lines(h_lines, angle_thresh=0.1, rho_thresh=30)
    v_clusters = _cluster_lines(v_lines, angle_thresh=0.1, rho_thresh=30)

    # Sort horizontal lines by rho (top to bottom on image).
    h_clusters.sort(key=lambda l: l[0])
    # Sort vertical lines by rho (left to right).
    v_clusters.sort(key=lambda l: l[0])

    confidence = min(1.0, (len(h_clusters) / 3) * (len(v_clusters) / 2) * 0.8)

    # We need at least 2 horizontal + 2 vertical lines to fit a court rectangle.
    if len(h_clusters) < 2 or len(v_clusters) < 2:
        geometry = _fallback_geometry(w, h)
        return geometry, max(0.1, confidence)

    # Pick the outermost horizontal and vertical lines as court boundaries.
    top_h = h_clusters[0]
    bot_h = h_clusters[-1]
    left_v = v_clusters[0]
    right_v = v_clusters[-1]

    tl = _line_intersection(top_h, left_v)
    tr = _line_intersection(top_h, right_v)
    bl = _line_intersection(bot_h, left_v)
    br = _line_intersection(bot_h, right_v)

    if None in (tl, tr, bl, br):
        geometry = _fallback_geometry(w, h)
        return geometry, 0.2

    geometry = CourtGeometry(
        top_left=CourtCorner(x=round(tl[0], 1), y=round(tl[1], 1)),
        top_right=CourtCorner(x=round(tr[0], 1), y=round(tr[1], 1)),
        bottom_left=CourtCorner(x=round(bl[0], 1), y=round(bl[1], 1)),
        bottom_right=CourtCorner(x=round(br[0], 1), y=round(br[1], 1)),
    )

    # Optionally write debug overlay.
    if output_path is not None:
        _write_overlay(median_bg.copy(), geometry, output_path)

    return geometry, round(min(confidence, 1.0), 3)


def _fallback_geometry(w: int, h: int) -> CourtGeometry:
    """Return a centered court estimate when detection fails."""
    margin_x, margin_y = w * 0.15, h * 0.15
    return CourtGeometry(
        top_left=CourtCorner(x=margin_x, y=margin_y),
        top_right=CourtCorner(x=w - margin_x, y=margin_y),
        bottom_left=CourtCorner(x=margin_x, y=h - margin_y),
        bottom_right=CourtCorner(x=w - margin_x, y=h - margin_y),
    )


_COURT_LINES_UV = [
    # Outer boundary
    (0, 0, 1, 0), (1, 0, 1, 1), (1, 1, 0, 1), (0, 1, 0, 0),
    # Kitchen lines (7ft from net; net at 22ft of 44ft court → v = 15/44 and 29/44)
    (0, 15/44, 1, 15/44), (0, 29/44, 1, 29/44),
    # Center lines (from kitchen line to nearest baseline)
    (0.5, 0, 0.5, 15/44), (0.5, 29/44, 0.5, 1),
]
_NET_UV = (0, 0.5, 1, 0.5)


def _lerp(a, b, t):
    return a + (b - a) * t


def _court_to_image(geo: CourtGeometry, u: float, v: float) -> tuple[int, int]:
    """Bilinear interpolation from normalized court (u,v) to image pixel coords."""
    tl, tr = geo.top_left, geo.top_right
    bl, br = geo.bottom_left, geo.bottom_right
    x = _lerp(_lerp(tl.x, tr.x, u), _lerp(bl.x, br.x, u), v)
    y = _lerp(_lerp(tl.y, tr.y, u), _lerp(bl.y, br.y, u), v)
    return int(x), int(y)


def _write_overlay(img: np.ndarray, geo: CourtGeometry, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for u0, v0, u1, v1 in _COURT_LINES_UV:
        cv2.line(img, _court_to_image(geo, u0, v0), _court_to_image(geo, u1, v1), (68, 170, 255), 2)
    u0, v0, u1, v1 = _NET_UV
    cv2.line(img, _court_to_image(geo, u0, v0), _court_to_image(geo, u1, v1), (0, 165, 255), 2)
    for key in ('top_left', 'top_right', 'bottom_left', 'bottom_right'):
        c = getattr(geo, key)
        cv2.circle(img, (int(c.x), int(c.y)), 6, (255, 100, 68), -1)
    cv2.imwrite(str(path), img)
