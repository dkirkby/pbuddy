"""Pass 1 orchestration — global scene & camera calibration."""

from __future__ import annotations

from pathlib import Path

import cv2  # type: ignore
import numpy as np

from pbva_core.dimensions import (
    COURT_TOTAL_LENGTH,
    COURT_TOTAL_WIDTH,
    VOLUME_BOUNDARY_EXTENSION,
    VOLUME_CORNER_HEIGHT,
    VOLUME_NET_HEIGHT,
)
from pbva_core.types import (
    CourtCorner,
    CourtGeometry,
    Pass1AcceptedOutput,
    Pass1CorrectionPayload,
    Pass1RawResult,
)
from pbva_pipeline.base import PassContext

from .scan import scan_video


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
        (-hw_ext, -hl_ext, 0 ),  # base corners
        ( hw_ext, -hl_ext, 0 ),
        ( hw_ext,  hl_ext, 0 ),
        (-hw_ext,  hl_ext, 0 ),
        (-hw_ext, -hl_ext, ch),  # top corners
        ( hw_ext, -hl_ext, ch),
        ( hw_ext,  hl_ext, ch),
        (-hw_ext,  hl_ext, ch),
        (-hw_ext,  0,      nh),  # tent peaks at net
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

def _default_court(bg_w: int, bg_h: int) -> CourtGeometry:
    """Return default court corners at normalized positions in the background image."""
    return CourtGeometry(
        top_left=CourtCorner(x=0.35 * bg_w, y=0.30 * bg_h),
        top_right=CourtCorner(x=0.65 * bg_w, y=0.30 * bg_h),
        bottom_left=CourtCorner(x=0.05 * bg_w, y=0.90 * bg_h),
        bottom_right=CourtCorner(x=0.95 * bg_w, y=0.90 * bg_h),
    )


class Pass1:
    name = "pass1"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")

    def run(self, ctx: PassContext, progress=None) -> Pass1RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        raw_dir = ctx.paths.pass_raw_dir
        bg_path = raw_dir / "median_background.png"

        # --- Single-pass scan: stable bounds + median background ---
        progress.update(0.0, "scan_video", "Scanning video…")
        progress.check_cancelled()

        def _scan_progress(frac: float, msg: str) -> None:
            progress.update(frac, "scan_video", msg)
            progress.check_cancelled()

        bounds, median_bg = scan_video(
            ctx.video_path,
            ctx.video_duration_s,
            target_samples=300,
            output_path=bg_path,
            progress_callback=_scan_progress,
            progress_start=0.0,
            progress_end=0.95,
        )
        progress.update(0.95, "scan_video",
                        f"Stable: {bounds.in_time_s:.1f}s – {bounds.out_time_s:.1f}s")

        # --- Write raw result JSON ---
        progress.update(0.95, "write_outputs", "Writing raw result…")
        bg_h, bg_w = median_bg.shape[:2]
        result = Pass1RawResult(
            stable_bounds=bounds,
            median_background_path=str(bg_path.relative_to(ctx.paths.project_root)),
            bg_width=bg_w,
            bg_height=bg_h,
        )
        (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
        progress.update(1.0, "write_outputs", "Pass 1 complete")

        return result

    def write_raw_outputs(self, ctx: PassContext, result: Pass1RawResult) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = [
            {"role": "raw", "type": "json", "path": str(raw_dir / "result.json")},
            {"role": "raw", "type": "png", "path": str(raw_dir / "median_background.png")},
        ]
        return [a for a in artifacts if Path(a["path"]).exists()]

    def validate_corrections(self, payload: dict) -> Pass1CorrectionPayload:
        return Pass1CorrectionPayload.model_validate(payload)

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass1RawResult,
        corrections: Pass1CorrectionPayload | None,
        median_bg_artifact_id: str = "",
    ) -> Pass1AcceptedOutput:
        bounds = raw_result.stable_bounds
        court_geo = (corrections.court_geometry if corrections and corrections.court_geometry
                     else _default_court(raw_result.bg_width, raw_result.bg_height))

        accepted = Pass1AcceptedOutput(
            stable_bounds=bounds,
            court_geometry=court_geo,
            median_background_artifact_id=median_bg_artifact_id,
            bg_width=raw_result.bg_width,
            bg_height=raw_result.bg_height,
        )

        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))

        mask = compute_tent_mask(court_geo, raw_result.bg_width, raw_result.bg_height)
        cv2.imwrite(str(accepted_dir / "tent_mask.png"), mask)

        return accepted
