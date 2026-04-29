"""Pass 1 orchestration — near-baseline court outline tracking."""

from __future__ import annotations

import math
from pathlib import Path

import cv2  # type: ignore
import numpy as np

from pbva_core.dimensions import (
    COURT_KV,
    COURT_TOTAL_LENGTH,
    COURT_TOTAL_WIDTH,
    VOLUME_BOUNDARY_EXTENSION,
    VOLUME_CORNER_HEIGHT,
    VOLUME_NET_HEIGHT,
)
from pbva_core.types import (
    CourtCorner,
    CourtGeometry,
    Pass0AcceptedOutput,
    Pass0RawResult,
    Pass1AcceptedOutput,
    Pass1CourtLine,
    Pass1RawResult,
    Pass1Sample,
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


# ─── Tent mask ────────────────────────────────────────────────────────────────

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


def compute_tent_mask(court_geo: CourtGeometry, bg_w: int, bg_h: int) -> np.ndarray:
    """Return a uint8 mask (bg_h × bg_w) with 255 inside the tent silhouette, 0 outside."""
    half_w = COURT_TOTAL_WIDTH  / 2
    half_l = COURT_TOTAL_LENGTH / 2
    hw_ext = half_w + VOLUME_BOUNDARY_EXTENSION
    hl_ext = half_l + VOLUME_BOUNDARY_EXTENSION
    ch     = VOLUME_CORNER_HEIGHT
    nh     = VOLUME_NET_HEIGHT

    vertices = [
        (-hw_ext, -hl_ext, 0 ),
        ( hw_ext, -hl_ext, 0 ),
        ( hw_ext,  hl_ext, 0 ),
        (-hw_ext,  hl_ext, 0 ),
        (-hw_ext, -hl_ext, ch),
        ( hw_ext, -hl_ext, ch),
        ( hw_ext,  hl_ext, ch),
        (-hw_ext,  hl_ext, ch),
        (-hw_ext,  0,      nh),
        ( hw_ext,  0,      nh),
    ]

    H = _build_homography(court_geo)
    M = np.array([
        [1 / (2 * half_w), 0,               0.5],
        [0,                1 / (2 * half_l), 0.5],
        [0,                0,               1  ],
    ])
    Hphys = H @ M

    cx, cy = bg_w / 2.0, bg_h / 2.0
    Hc = np.array([
        [Hphys[0, 0] - cx * Hphys[2, 0],  Hphys[0, 1] - cx * Hphys[2, 1],  Hphys[0, 2] - cx * Hphys[2, 2]],
        [Hphys[1, 0] - cy * Hphys[2, 0],  Hphys[1, 1] - cy * Hphys[2, 1],  Hphys[1, 2] - cy * Hphys[2, 2]],
        [Hphys[2, 0],                      Hphys[2, 1],                      Hphys[2, 2]                    ],
    ])
    h1, h2 = Hc[:, 0], Hc[:, 1]

    num, denom = h1[0] * h2[0] + h1[1] * h2[1], h1[2] * h2[2]
    if abs(denom) < 1e-12 or num / denom > 0:
        f = np.sqrt(abs(num / denom) or 1) * (bg_w + bg_h) / 4
    else:
        f = np.sqrt(-num / denom)

    K     = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
    K_inv = np.array([[1/f, 0, -cx/f], [0, 1/f, -cy/f], [0, 0, 1]])

    r1_raw = K_inv @ Hphys[:, 0]
    r2_raw = K_inv @ Hphys[:, 1]
    t_raw  = K_inv @ Hphys[:, 2]

    lam = float(np.linalg.norm(r1_raw))
    r1  = r1_raw / lam
    r2  = r2_raw / lam
    r3  = np.cross(r1, r2)
    t   = t_raw  / lam

    Rt = np.column_stack([r1, r2, r3, t])
    P  = K @ Rt

    projected = []
    for X, Y, Z in vertices:
        uvw = P @ np.array([X, Y, -Z, 1.0])
        if uvw[2] > 0:
            projected.append([uvw[0] / uvw[2], uvw[1] / uvw[2]])

    if len(projected) < 3:
        return np.full((bg_h, bg_w), 255, dtype=np.uint8)

    pts  = np.array(projected, dtype=np.float32)
    hull = cv2.convexHull(pts)
    mask = np.zeros((bg_h, bg_w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, hull.astype(np.int32), 255)
    return mask


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


def _sample_court_line(
    H: np.ndarray,
    u0: float, v0: float,
    u1: float, v1: float,
    n_interior: int,
    cx: float, cy: float,
    k1: float, scale: float,
    perp_seg_length_px: float,
    perp_seg_points: int,
    single_ch: np.ndarray,
) -> list[Pass1SamplePoint]:
    """Sample n_interior interior points equally spaced by arc length along the
    distorted image curve traced by court line (u0,v0)→(u1,v1).

    The curve is densely sampled in court-coord space and mapped to distorted
    image coordinates; cumulative arc length is computed, then np.interp places
    the interior points at equal arc-length fractions.  The local tangent at
    each point is estimated by a central finite difference along the arc.
    """
    DENSE = 500

    # Dense sample of the distorted curve.
    ts = np.linspace(0.0, 1.0, DENSE + 1)
    du, dv = u1 - u0, v1 - v0
    pts = np.array([
        _court_to_image(H, u0 + t * du, v0 + t * dv, cx, cy, k1, scale)
        for t in ts
    ])  # (DENSE+1, 2)

    # Cumulative arc length along the distorted curve.
    seg_lens = np.hypot(*(np.diff(pts, axis=0).T))
    cumlen = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total = float(cumlen[-1])
    if total < 1e-6:
        raise ValueError(f"Degenerate court line in image space: ({u0},{v0}) → ({u1},{v1})")

    # Arc-length positions for equally-spaced interior points.
    targets = np.linspace(0.0, total, n_interior + 2)[1:-1]

    sx_vals = np.interp(targets, cumlen, pts[:, 0])
    sy_vals = np.interp(targets, cumlen, pts[:, 1])

    # Local tangent via central difference along the arc (1% of total length).
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

    n_samp = max(2, perp_seg_points)
    points: list[Pass1SamplePoint] = []

    for sx, sy, perp_x, perp_y in zip(sx_vals, sy_vals, perp_xs, perp_ys):
        sx, sy = float(sx), float(sy)
        perp_x, perp_y = float(perp_x), float(perp_y)

        px1 = sx + perp_seg_length_px * perp_x
        py1 = sy + perp_seg_length_px * perp_y
        px2 = sx - perp_seg_length_px * perp_x
        py2 = sy - perp_seg_length_px * perp_y

        samples: list[Pass1Sample] = []
        for j in range(n_samp):
            s = -1.0 + 2.0 * j / (n_samp - 1)
            samp_x = sx + s * perp_seg_length_px * perp_x
            samp_y = sy + s * perp_seg_length_px * perp_y
            samples.append(Pass1Sample(
                s=round(s, 6),
                x=round(samp_x, 2),
                y=round(samp_y, 2),
                val=round(_bilinear(single_ch, samp_x, samp_y), 3),
            ))

        points.append(Pass1SamplePoint(
            sx=round(sx, 2), sy=round(sy, 2),
            px1=round(px1, 2), py1=round(py1, 2),
            px2=round(px2, 2), py2=round(py2, 2),
            samples=samples,
        ))

    return points


def track_court_outline(
    corners: CourtGeometry,
    k1: float,
    pass0_medians_dir: Path,
    median_index: int,
    perp_seg_length_px: float = 64,
    perp_seg_points: int = 64,
) -> tuple[np.ndarray, list[Pass1CourtLine]]:
    """Sample perpendicular profiles across the near baseline and near sidelines.

    Returns (single_channel_image, court_lines) where court_lines contains
    five Pass1CourtLine objects.
    """
    # Load and convert median image to single channel V - S/2.
    median_path = pass0_medians_dir / f"median_{median_index:03d}.png"
    bgr = cv2.imread(str(median_path))
    if bgr is None:
        raise FileNotFoundError(f"Median image not found: {median_path}")

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.int16)
    single = np.clip(hsv[:, :, 2] - hsv[:, :, 1] // 2, 0, 255).astype(np.uint8)
    single = cv2.GaussianBlur(single, (0, 0), sigmaX=2, sigmaY=2)

    bg_h, bg_w = bgr.shape[:2]
    cx, cy = bg_w / 2.0, bg_h / 2.0
    scale = math.sqrt(cx * cx + cy * cy)

    # Build homography from undistorted corners so court coords → undistorted image.
    def _uc(c: CourtCorner) -> CourtCorner:
        ux, uy = _undistort(c.x, c.y, cx, cy, k1, scale)
        return CourtCorner(x=ux, y=uy)

    H = _build_homography(CourtGeometry(
        top_left=_uc(corners.top_left),
        top_right=_uc(corners.top_right),
        bottom_left=_uc(corners.bottom_left),
        bottom_right=_uc(corners.bottom_right),
    ))

    kwargs: dict = dict(
        H=H, cx=cx, cy=cy, k1=k1, scale=scale,
        perp_seg_length_px=perp_seg_length_px,
        perp_seg_points=perp_seg_points,
        single_ch=single,
    )

    # Near kitchen line is at v = 1 - COURT_KV in normalised court coords.
    kitchen_v = 1 - COURT_KV

    # Baseline: 12 equally-spaced interior points; use pts[1:5] (left side) and
    # pts[7:11] (right side), dropping points adjacent to corners and near the centre.
    baseline_all = _sample_court_line(u0=0, v0=1, u1=1, v1=1, n_interior=12, **kwargs)

    # Sidelines: 5 equally-spaced interior points; drop pts[0] (closest to corner).
    left_all  = _sample_court_line(u0=0, v0=1, u1=0, v1=kitchen_v, n_interior=5, **kwargs)
    right_all = _sample_court_line(u0=1, v0=1, u1=1, v1=kitchen_v, n_interior=5, **kwargs)

    court_lines = [
        Pass1CourtLine(
            name="near_baseline", color="#0ff",
            points=baseline_all[1:5] + baseline_all[7:11],
        ),
        Pass1CourtLine(
            name="left_sideline", color="#f0f",
            points=left_all[1:5],
        ),
        Pass1CourtLine(
            name="right_sideline", color="#ff0",
            points=right_all[1:5],
        ),
        Pass1CourtLine(
            name="near_centerline", color="#0f0",
            points=_sample_court_line(u0=0.5, v0=1,         u1=0.5, v1=kitchen_v, n_interior=4, **kwargs),
        ),
        Pass1CourtLine(
            name="near_kitchen_line", color="#f80",
            points=_sample_court_line(u0=0,   v0=kitchen_v, u1=1,   v1=kitchen_v, n_interior=4, **kwargs),
        ),
    ]

    return single, court_lines


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

        progress.update(0.2, "track_outline", "Computing baseline sample points…")
        progress.check_cancelled()

        perp_seg_length_px: float = 64
        perp_seg_points: int = 64
        single_ch, court_lines = track_court_outline(
            corners=pass0_accepted.court_geometry,
            k1=pass0_accepted.k1,
            pass0_medians_dir=medians_dir,
            median_index=median_index,
            perp_seg_length_px=perp_seg_length_px,
            perp_seg_points=perp_seg_points,
        )

        progress.update(0.8, "write_outputs", "Writing raw outputs…")
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(raw_dir / "single_channel.png"), single_ch)

        bg_h, bg_w = single_ch.shape[:2]
        result = Pass1RawResult(
            bg_width=bg_w,
            bg_height=bg_h,
            median_chunk_index=median_index,
            perp_seg_length_px=perp_seg_length_px,
            perp_seg_points=perp_seg_points,
            court_lines=court_lines,
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))

        progress.update(1.0, "write_outputs", "Pass 1 complete")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass1RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        return [
            a for a in [
                {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
                {"role": "raw", "type": "png",  "path": str(raw_dir / "single_channel.png")},
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
        pass0_accepted_path = ctx.paths.project_root / "passes" / "pass0" / "accepted" / "result.json"
        if not pass0_accepted_path.exists():
            raise FileNotFoundError("Pass 0 accepted output not found; accept Pass 0 before accepting Pass 1")
        pass0 = Pass0AcceptedOutput.model_validate_json(pass0_accepted_path.read_text())

        accepted = Pass1AcceptedOutput(bg_width=raw_result.bg_width, bg_height=raw_result.bg_height)

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))

        mask = compute_tent_mask(pass0.court_geometry, raw_result.bg_width, raw_result.bg_height)
        cv2.imwrite(str(accepted_dir / "tent_mask.png"), mask)

        return accepted
