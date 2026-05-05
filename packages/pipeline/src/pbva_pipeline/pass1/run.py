"""Pass 1 orchestration — near-baseline court outline tracking."""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2  # type: ignore
import numpy as np

from pbva_core.dimensions import COURT_KV, COURT_TOTAL_LENGTH, COURT_TOTAL_WIDTH
from pbva_core.types import (
    CourtCorner,
    CourtGeometry,
    Pass0AcceptedOutput,
    Pass0RawResult,
    Pass1AcceptedOutput,
    Pass1ChunkProfiles,
    Pass1ChunkVertices,
    Pass1CourtLine,
    Pass1RawResult,
    Pass1SegmentAnalysis,
    Pass1SamplePoint,
)
from pbva_pipeline.base import PassContext


# ─── Distortion helpers (match Pass0Page.tsx single-term division model) ──────
# scale = image half-diagonal; normalises r so k1 is dimensionless and ~O(1).

def _undistort(xd: float, yd: float, cx: float, cy: float, k1: float, scale: float) -> tuple[float, float]:
    dx, dy = (xd - cx) / scale, (yd - cy) / scale
    r2 = dx * dx + dy * dy
    if abs(k1) < 1e-9 or r2 < 1e-9:
        return xd, yd
    rd = math.sqrt(r2)
    ru = rd / (1 + k1 * rd * rd)
    f = ru / rd
    return cx + dx * f * scale, cy + dy * f * scale


def _distort(xu: float, yu: float, cx: float, cy: float, k1: float, scale: float) -> tuple[float, float]:
    dx, dy = (xu - cx) / scale, (yu - cy) / scale
    r2 = dx * dx + dy * dy
    if abs(k1) < 1e-9 or r2 < 1e-9:
        return xu, yu
    ru = math.sqrt(r2)
    disc = 1 - 4 * k1 * r2
    if disc < 0:
        return xu, yu
    rd = (1 - math.sqrt(disc)) / (2 * k1 * ru)
    f = rd / ru
    return cx + dx * f * scale, cy + dy * f * scale


# ─── Homography ───────────────────────────────────────────────────────────────

def _build_homography(g: CourtGeometry) -> np.ndarray:
    """Build the 3×3 homography mapping (u,v,1) → (px,py,w) from court corners."""
    TL, TR = g.top_left,    g.top_right
    BL, BR = g.bottom_left, g.bottom_right

    A = TR.x - BR.x;  B = BL.x - BR.x
    C = TL.x - TR.x - BL.x + BR.x
    D = TR.y - BR.y;  E = BL.y - BR.y
    F = TL.y - TR.y - BL.y + BR.y
    det = A * E - B * D
    gh = (C * E - B * F) / det
    hh = (A * F - C * D) / det

    return np.array([
        [TR.x * (gh + 1) - TL.x,  BL.x * (hh + 1) - TL.x,  TL.x],
        [TR.y * (gh + 1) - TL.y,  BL.y * (hh + 1) - TL.y,  TL.y],
        [gh,                       hh,                        1   ],
    ])


# ─── Bilinear interpolation ───────────────────────────────────────────────────

def _bilinear(img: np.ndarray, x: float, y: float) -> float:
    h, w = img.shape
    x = max(0.0, min(float(w - 1), x))
    y = max(0.0, min(float(h - 1), y))
    x0, y0 = int(x), int(y)
    x1 = min(x0 + 1, w - 1)
    y1 = min(y0 + 1, h - 1)
    dx, dy = x - x0, y - y0
    return (
        (1 - dx) * (1 - dy) * float(img[y0, x0])
        + dx * (1 - dy) * float(img[y0, x1])
        + (1 - dx) * dy * float(img[y1, x0])
        + dx * dy * float(img[y1, x1])
    )


# ─── Core algorithm ───────────────────────────────────────────────────────────

def _court_to_image(
    H: np.ndarray, u: float, v: float,
    cx: float, cy: float, k1: float, scale: float,
) -> tuple[float, float]:
    """Map normalised court coordinate (u,v) → distorted image coordinate."""
    w  = H[2, 0] * u + H[2, 1] * v + H[2, 2]
    xu = (H[0, 0] * u + H[0, 1] * v + H[0, 2]) / w
    yu = (H[1, 0] * u + H[1, 1] * v + H[1, 2]) / w
    return _distort(xu, yu, cx, cy, k1, scale)


def _compute_line_geometry(
    H: np.ndarray,
    u0: float, v0: float,
    u1: float, v1: float,
    n_interior: int,
    cx: float, cy: float,
    k1: float, scale: float,
    perp_seg_length_px: float,
) -> list[Pass1SamplePoint]:
    """Compute n_interior sample-point geometries equally spaced by arc length
    along the distorted image curve traced by court line (u0,v0)→(u1,v1).

    Returns Pass1SamplePoint objects (positions only, no sample values).
    """
    DENSE = 500

    ts = np.linspace(0.0, 1.0, DENSE + 1)
    du, dv = u1 - u0, v1 - v0
    pts = np.array([
        _court_to_image(H, u0 + t * du, v0 + t * dv, cx, cy, k1, scale)
        for t in ts
    ])  # (DENSE+1, 2)

    seg_lens = np.hypot(*(np.diff(pts, axis=0).T))
    cumlen = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total = float(cumlen[-1])
    if total < 1e-6:
        raise ValueError(f"Degenerate court line in image space: ({u0},{v0}) → ({u1},{v1})")

    targets = np.linspace(0.0, total, n_interior + 2)[1:-1]
    sx_vals = np.interp(targets, cumlen, pts[:, 0])
    sy_vals = np.interp(targets, cumlen, pts[:, 1])

    h = total * 0.01
    fwd = np.minimum(targets + h, total)
    bwd = np.maximum(targets - h, 0.0)
    tan_x = np.interp(fwd, cumlen, pts[:, 0]) - np.interp(bwd, cumlen, pts[:, 0])
    tan_y = np.interp(fwd, cumlen, pts[:, 1]) - np.interp(bwd, cumlen, pts[:, 1])
    tan_len = np.hypot(tan_x, tan_y)
    tan_x /= tan_len
    tan_y /= tan_len
    perp_xs = -tan_y  # 90° CCW
    perp_ys =  tan_x

    points: list[Pass1SamplePoint] = []
    for sx, sy, perp_x, perp_y in zip(sx_vals, sy_vals, perp_xs, perp_ys):
        sx, sy = float(sx), float(sy)
        perp_x, perp_y = float(perp_x), float(perp_y)
        px1 = sx + perp_seg_length_px * perp_x
        py1 = sy + perp_seg_length_px * perp_y
        px2 = sx - perp_seg_length_px * perp_x
        py2 = sy - perp_seg_length_px * perp_y
        points.append(Pass1SamplePoint(
            sx=round(sx, 2), sy=round(sy, 2),
            px1=round(px1, 2), py1=round(py1, 2),
            px2=round(px2, 2), py2=round(py2, 2),
        ))

    return points


def _sample_line_vals(
    single_ch: np.ndarray,
    points: list[Pass1SamplePoint],
    perp_seg_length_px: float,
    perp_seg_points: int,
) -> list[list[float]]:
    """Sample the blurred V−S/2 image along each point's perpendicular segment.

    Returns vals[point_idx][sample_idx].  Perpendicular direction is recovered
    from the stored (px1,py1) endpoint and (sx,sy) centre.
    """
    n_samp = max(2, perp_seg_points)
    result: list[list[float]] = []
    for pt in points:
        perp_x = (pt.px1 - pt.sx) / perp_seg_length_px
        perp_y = (pt.py1 - pt.sy) / perp_seg_length_px
        vals: list[float] = []
        for j in range(n_samp):
            s = -1.0 + 2.0 * j / (n_samp - 1)
            vals.append(round(_bilinear(single_ch, pt.sx + s * perp_seg_length_px * perp_x,
                                                   pt.sy + s * perp_seg_length_px * perp_y), 3))
        result.append(vals)
    return result


def track_court_outline(
    corners: CourtGeometry,
    k1: float,
    pass0_medians_dir: Path,
    median_index: int,
    perp_seg_length_px: float = 64,
    perp_seg_points: int = 128,
    on_chunk: object = None,
) -> tuple[int, int, list[Pass1CourtLine], list[Pass1ChunkProfiles]]:
    """Compute perpendicular-segment geometry and sample all pass0 medians.

    Returns (bg_width, bg_height, court_lines, chunks) where court_lines holds
    the chunk-independent geometry and chunks holds per-median sampled values.
    """
    # Use midpoint median for image dimensions and homography (geometry is chunk-independent).
    midpoint_path = pass0_medians_dir / f"median_{median_index:03d}.png"
    bgr_mid = cv2.imread(str(midpoint_path))
    if bgr_mid is None:
        raise FileNotFoundError(f"Median image not found: {midpoint_path}")

    bg_h, bg_w = bgr_mid.shape[:2]
    cx, cy = bg_w / 2.0, bg_h / 2.0
    scale = math.sqrt(cx * cx + cy * cy)

    def _uc(c: CourtCorner) -> CourtCorner:
        ux, uy = _undistort(c.x, c.y, cx, cy, k1, scale)
        return CourtCorner(x=ux, y=uy)

    H = _build_homography(CourtGeometry(
        top_left=_uc(corners.top_left),
        top_right=_uc(corners.top_right),
        bottom_left=_uc(corners.bottom_left),
        bottom_right=_uc(corners.bottom_right),
    ))

    geo_kw: dict = dict(H=H, cx=cx, cy=cy, k1=k1, scale=scale, perp_seg_length_px=perp_seg_length_px)
    kitchen_v = 1 - COURT_KV

    # ── Geometry (chunk-independent) ──────────────────────────────────────────
    baseline_all = _compute_line_geometry(u0=0,   v0=1,         u1=1,   v1=1,         n_interior=12, **geo_kw)
    left_all     = _compute_line_geometry(u0=0,   v0=1,         u1=0,   v1=kitchen_v, n_interior=5,  **geo_kw)
    right_all    = _compute_line_geometry(u0=1,   v0=1,         u1=1,   v1=kitchen_v, n_interior=5,  **geo_kw)

    court_lines = [
        Pass1CourtLine(name="near_baseline",    color="#0ff", points=baseline_all[1:5] + baseline_all[7:11]),
        Pass1CourtLine(name="left_sideline",    color="#f0f", points=left_all[1:5]),
        Pass1CourtLine(name="right_sideline",   color="#ff0", points=right_all[1:5]),
        Pass1CourtLine(name="near_centerline",  color="#0f0",
                       points=_compute_line_geometry(u0=0.5, v0=1, u1=0.5, v1=kitchen_v, n_interior=4, **geo_kw)),
        Pass1CourtLine(name="near_kitchen_line", color="#f80",
                       points=_compute_line_geometry(u0=0, v0=kitchen_v, u1=1, v1=kitchen_v, n_interior=4, **geo_kw)),
    ]

    # ── Per-chunk sampling ─────────────────────────────────────────────────────
    median_paths = sorted(pass0_medians_dir.glob("median_*.png"))
    chunks: list[Pass1ChunkProfiles] = []

    for idx, path in enumerate(median_paths):
        if callable(on_chunk):
            on_chunk(idx, len(median_paths))
        chunk_index = int(path.stem.split("_")[1])
        bgr = cv2.imread(str(path))
        if bgr is None:
            continue
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.int16)
        single = np.clip(hsv[:, :, 2] - hsv[:, :, 1] // 2, 0, 255).astype(np.uint8)
        single = cv2.GaussianBlur(single, (0, 0), sigmaX=2, sigmaY=2)
        vals = [
            _sample_line_vals(single, line.points, perp_seg_length_px, perp_seg_points)
            for line in court_lines
        ]
        chunks.append(Pass1ChunkProfiles(chunk_index=chunk_index, vals=vals))

    chunks.sort(key=lambda c: c.chunk_index)
    return bg_w, bg_h, court_lines, chunks


# ─── Segment analysis ────────────────────────────────────────────────────────

def _analyse_segments(
    court_lines: list[Pass1CourtLine],
    chunks: list[Pass1ChunkProfiles],
    perp_seg_length_px: float,
    perp_seg_points: int,
    on_segment: object = None,
) -> list[list[Pass1SegmentAnalysis]]:
    """For each segment, compute a robust gradient reference curve, per-chunk lags, and image positions."""
    import io
    import contextlib
    from pbva_pipeline.zncc import robust_reference_curve

    nchunks = len(chunks)
    blank_ref = [0.0] * perp_seg_points
    pix_per_sample = 2.0 * perp_seg_length_px / (perp_seg_points - 1)
    total_segments = sum(len(line.points) for line in court_lines)
    segment_idx = 0
    result: list[list[Pass1SegmentAnalysis]] = []

    for li, line in enumerate(court_lines):
        line_analyses: list[Pass1SegmentAnalysis] = []
        for pi, pt in enumerate(line.points):
            if callable(on_segment):
                on_segment(segment_idx, total_segments)
            segment_idx += 1

            if nchunks < 2:
                lags: list[float | None] = [None] * nchunks
                sims: list[float | None] = [None] * nchunks
                positions: list[list[float] | None] = [None] * nchunks
                line_analyses.append(Pass1SegmentAnalysis(
                    reference=blank_ref, lags=lags, similarities=sims, positions=positions,
                    is_interpolated=[False] * nchunks, is_outlier=[False] * nchunks,
                ))
                continue

            raw = np.array([chunks[ci].vals[li][pi] for ci in range(nchunks)], dtype=float)
            grad_curves = np.gradient(raw, axis=1)

            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    reference, lags_arr, sims_arr, was_nan = robust_reference_curve(grad_curves)
                ref = [round(float(v), 4) for v in reference]
                lags = [None if np.isnan(v) else round(float(v), 4) for v in lags_arr]
                sims = [None if np.isnan(v) else round(float(v), 4) for v in sims_arr]
                is_interp = [bool(w) for w in was_nan]
            except Exception:
                ref = blank_ref
                lags = [None] * nchunks
                sims = [None] * nchunks
                is_interp = [False] * nchunks

            perp_x = (pt.px1 - pt.sx) / perp_seg_length_px
            perp_y = (pt.py1 - pt.sy) / perp_seg_length_px
            positions = [
                None if lag is None else [
                    round(pt.sx + lag * pix_per_sample * perp_x, 2),
                    round(pt.sy + lag * pix_per_sample * perp_y, 2),
                ]
                for lag in lags
            ]

            line_analyses.append(Pass1SegmentAnalysis(
                reference=ref, lags=lags, similarities=sims, positions=positions,
                is_interpolated=is_interp, is_outlier=[False] * nchunks,
            ))
        result.append(line_analyses)

    return result


# ─── Vertex computation ───────────────────────────────────────────────────────

def _fit_line(points: np.ndarray) -> np.ndarray | None:
    """Fit ax + by + c = 0 via total least squares (SVD). Returns unit-normalised (a,b,c) or None."""
    if len(points) < 2:
        return None
    centroid = points.mean(axis=0)
    _, _, Vt = np.linalg.svd(points - centroid, full_matrices=False)
    a, b = Vt[-1]  # normal to the best-fit line
    c = -(a * centroid[0] + b * centroid[1])
    norm = math.sqrt(a * a + b * b)
    return np.array([a / norm, b / norm, c / norm])


def _intersect(l1: np.ndarray, l2: np.ndarray) -> list[float] | None:
    """Intersection of two homogeneous lines (a,b,c). Returns [x, y] or None if parallel."""
    cross = np.cross(l1, l2)
    if abs(cross[2]) < 1e-9:
        return None
    return [round(float(cross[0] / cross[2]), 2), round(float(cross[1] / cross[2]), 2)]


_OUTLIER_FACTOR = 2.0   # max residual must exceed this multiple of others' RMS
_OUTLIER_MIN_PX = 3.0   # and must exceed this absolute threshold (undistorted px)


def _compute_chunk_vertices(
    segment_analyses: list[list[Pass1SegmentAnalysis]],
    court_lines: list[Pass1CourtLine],
    chunks: list[Pass1ChunkProfiles],
    cx: float, cy: float, k1: float, scale: float,
) -> list[Pass1ChunkVertices]:
    """For each chunk, undistort lag-derived positions, fit one line per court line
    (with outlier detection and refit when ≥3 points), then intersect adjacent pairs
    to obtain the 6 near-side court vertices.

    Outlier flags are written back into segment_analyses in place.
    """
    line_idx = {line.name: li for li, line in enumerate(court_lines)}

    results: list[Pass1ChunkVertices] = []
    for ci, chunk in enumerate(chunks):
        fitted: dict[str, np.ndarray | None] = {}
        for name, li in line_idx.items():
            if li >= len(segment_analyses):
                fitted[name] = None
                continue

            # Collect (xu, yu, pi) for each segment with a valid position.
            pts: list[tuple[float, float, int]] = []
            for pi, analysis in enumerate(segment_analyses[li]):
                pos = analysis.positions[ci] if ci < len(analysis.positions) else None
                if pos is not None:
                    xu, yu = _undistort(pos[0], pos[1], cx, cy, k1, scale)
                    pts.append((xu, yu, pi))

            if len(pts) < 2:
                fitted[name] = None
                continue

            arr = np.array([[x, y] for x, y, _ in pts])

            if len(pts) >= 3:
                # Leave-one-out residuals: fit through n-1 points, measure left-out point's distance.
                loo_res = np.zeros(len(pts))
                for i in range(len(pts)):
                    loo_line = _fit_line(np.delete(arr, i, axis=0))
                    if loo_line is not None:
                        loo_res[i] = abs(float(arr[i] @ loo_line[:2] + loo_line[2]))

                max_idx = int(np.argmax(loo_res))
                max_res = float(loo_res[max_idx])
                others_rms = float(np.sqrt(np.mean(np.delete(loo_res, max_idx) ** 2)))

                if max_res > _OUTLIER_FACTOR * others_rms and max_res > _OUTLIER_MIN_PX:
                    segment_analyses[li][pts[max_idx][2]].is_outlier[ci] = True
                    arr = np.delete(arr, max_idx, axis=0)

            fitted[name] = _fit_line(arr)

        def inter(n1: str, n2: str) -> list[float] | None:
            l1, l2 = fitted.get(n1), fitted.get(n2)
            return None if l1 is None or l2 is None else _intersect(l1, l2)

        results.append(Pass1ChunkVertices(
            chunk_index=chunk.chunk_index,
            baseline_left=inter("near_baseline", "left_sideline"),
            baseline_right=inter("near_baseline", "right_sideline"),
            baseline_center=inter("near_baseline", "near_centerline"),
            kitchen_left=inter("near_kitchen_line", "left_sideline"),
            kitchen_right=inter("near_kitchen_line", "right_sideline"),
            kitchen_center=inter("near_kitchen_line", "near_centerline"),
        ))
    return results


# ─── Camera model (far-corner extrapolation) ──────────────────────────────────

# Physical coordinates of each named near-side vertex (x=left→right, y=near→far, metres).
_W = COURT_TOTAL_WIDTH
_L = COURT_TOTAL_LENGTH
_D = COURT_KV * _L   # near-baseline to near-kitchen distance

_VERTEX_PHYS: dict[str, tuple[float, float]] = {
    "baseline_left":   (0.0,    0.0),
    "baseline_right":  (_W,     0.0),
    "baseline_center": (_W / 2, 0.0),
    "kitchen_left":    (0.0,    _D),
    "kitchen_right":   (_W,     _D),
    "kitchen_center":  (_W / 2, _D),
}


def _dlt_homography(phys: np.ndarray, img: np.ndarray) -> np.ndarray | None:
    """Fit 3×3 homography H so that H @ [x,y,1]^T ∝ [u,v,1]^T (undistorted image).

    phys, img : (n, 2) arrays of corresponding physical / undistorted-image coords.
    Returns H or None if the system is degenerate.
    """
    n = len(phys)
    if n < 4:
        return None
    A = np.zeros((2 * n, 9))
    for i, ((x, y), (u, v)) in enumerate(zip(phys, img)):
        A[2 * i]     = [x, y, 1,  0, 0, 0,  -u * x, -u * y, -u]
        A[2 * i + 1] = [0, 0, 0,  x, y, 1,  -v * x, -v * y, -v]
    _, _, Vt = np.linalg.svd(A)
    h = Vt[-1]
    if abs(h[8]) < 1e-12:
        return None
    return h.reshape(3, 3)


def _project(H: np.ndarray, x: float, y: float) -> tuple[float, float]:
    """Map physical (x,y) → undistorted image (u,v) via homography H."""
    w = H @ np.array([x, y, 1.0])
    return float(w[0] / w[2]), float(w[1] / w[2])


def _compute_camera_model(
    chunk_vertices: list[Pass1ChunkVertices],
    cx: float, cy: float, k1: float, scale: float,
    video_fps: float,
    video_duration_s: float,
    median_count: int,
) -> list[dict]:
    """For each chunk, fit a physical→undistorted homography from the near-side vertices,
    project the 4 full-court corners, and distort to image pixel coordinates.

    Corners are returned in order: near-left, near-right, far-right, far-left (CCW from above).
    """
    total_frames = video_fps * video_duration_s
    entries: list[dict] = []

    for cv in chunk_vertices:
        chunk_id = cv.chunk_index
        midpt_frame = int((chunk_id + 0.5) * total_frames / median_count)
        midpt_time_sec = round(midpt_frame / video_fps, 3)

        phys_list: list[list[float]] = []
        img_list: list[list[float]] = []
        for name, (xp, yp) in _VERTEX_PHYS.items():
            pos: list[float] | None = getattr(cv, name)
            if pos is not None:
                phys_list.append([xp, yp])
                img_list.append(pos)  # already undistorted image coords

        H = _dlt_homography(np.array(phys_list), np.array(img_list)) if len(phys_list) >= 4 else None

        if H is None:
            entries.append({
                "chunk_id": chunk_id,
                "midpt_frame": midpt_frame,
                "midpt_time_sec": midpt_time_sec,
                "img_corners_px": None,
            })
            continue

        # Project the 4 court corners: near-left, near-right, far-right, far-left
        corner_phys = [(0.0, 0.0), (_W, 0.0), (_W, _L), (0.0, _L)]
        corners = []
        for xp, yp in corner_phys:
            xu, yu = _project(H, xp, yp)
            xd, yd = _distort(xu, yu, cx, cy, k1, scale)
            corners.append({"x": round(xd, 2), "y": round(yd, 2)})

        entries.append({
            "chunk_id": chunk_id,
            "midpt_frame": midpt_frame,
            "midpt_time_sec": midpt_time_sec,
            "img_corners_px": corners,
        })

    return entries


# ─── Pass class ───────────────────────────────────────────────────────────────

class Pass1:
    name = "pass1"

    def validate_inputs(self, ctx: PassContext) -> None:
        pass0_accepted = ctx.paths.project_root / "passes" / "pass0" / "accepted" / "result.json"
        if not pass0_accepted.exists():
            raise FileNotFoundError("Pass 0 accepted output not found; run and accept Pass 0 first")
        medians_dir = ctx.paths.project_root / "passes" / "pass0" / "raw" / "medians"
        if not medians_dir.exists() or not any(medians_dir.glob("median_*.png")):
            raise FileNotFoundError("Pass 0 median images not found; run Pass 0 first")

    def run(self, ctx: PassContext, progress=None) -> Pass1RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.0, "setup", "Loading Pass 0 accepted output…")
        progress.check_cancelled()

        pass0_accepted_path = ctx.paths.project_root / "passes" / "pass0" / "accepted" / "result.json"
        pass0_accepted = Pass0AcceptedOutput.model_validate_json(pass0_accepted_path.read_text())

        pass0_raw_path = ctx.paths.project_root / "passes" / "pass0" / "raw" / "result.json"
        pass0_raw = Pass0RawResult.model_validate_json(pass0_raw_path.read_text())

        medians_dir = ctx.paths.project_root / "passes" / "pass0" / "raw" / "medians"
        median_index = pass0_raw.midpoint_chunk

        progress.update(0.02, "track_outline", "Computing court-line geometry…")
        progress.check_cancelled()

        perp_seg_length_px: float = 64
        perp_seg_points: int = 128

        def _on_chunk(i: int, total: int) -> None:
            progress.update(0.02 + 0.13 * i / total, "sampling", f"Sampling median {i + 1}/{total}…")
            progress.check_cancelled()

        bg_w, bg_h, court_lines, chunks = track_court_outline(
            corners=pass0_accepted.court_geometry,
            k1=pass0_accepted.k1,
            pass0_medians_dir=medians_dir,
            median_index=median_index,
            perp_seg_length_px=perp_seg_length_px,
            perp_seg_points=perp_seg_points,
            on_chunk=_on_chunk,
        )

        def _on_segment(i: int, total: int) -> None:
            progress.update(0.15 + 0.82 * i / total, "analyse", f"Analysing segment {i + 1}/{total}…")
            progress.check_cancelled()

        segment_analyses = _analyse_segments(court_lines, chunks, perp_seg_length_px, perp_seg_points, on_segment=_on_segment)

        progress.update(0.97, "vertices", "Computing court line vertices…")
        progress.check_cancelled()
        cx = bg_w / 2.0
        cy = bg_h / 2.0
        scale = math.sqrt(cx * cx + cy * cy)
        chunk_vertices = _compute_chunk_vertices(
            segment_analyses, court_lines, chunks, cx, cy, pass0_accepted.k1, scale,
        )
        camera_model = _compute_camera_model(
            chunk_vertices, cx, cy, pass0_accepted.k1, scale,
            ctx.video_fps, ctx.video_duration_s, pass0_raw.median_count,
        )

        progress.update(0.98, "write_outputs", "Writing raw outputs…")
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        result = Pass1RawResult(
            bg_width=bg_w,
            bg_height=bg_h,
            midpoint_chunk_index=median_index,
            perp_seg_length_px=perp_seg_length_px,
            perp_seg_points=perp_seg_points,
            k1=pass0_accepted.k1,
            court_lines=court_lines,
            chunks=chunks,
            segment_analyses=segment_analyses,
            chunk_vertices=chunk_vertices,
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
        (raw_dir / "camera-model.json").write_text(json.dumps(camera_model, indent=2))

        progress.update(1.0, "write_outputs", "Pass 1 complete")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass1RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        return [
            a for a in [
                {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
                {"role": "raw", "type": "json", "path": str(raw_dir / "camera-model.json")},
            ]
            if Path(a["path"]).exists()
        ]

    def validate_corrections(self, payload: dict) -> object:
        from pbva_core.types import Pass1CorrectionPayload
        return Pass1CorrectionPayload.model_validate(payload)

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass1RawResult,
    ) -> Pass1AcceptedOutput:
        accepted = Pass1AcceptedOutput(bg_width=raw_result.bg_width, bg_height=raw_result.bg_height)
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
