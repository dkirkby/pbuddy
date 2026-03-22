"""Frame-by-frame background subtraction and blob detection for Pass 2."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# Default parameters.
DEFAULT_THRESHOLD = 20
DEFAULT_MIN_AREA = 50         # px² — small enough to catch a ~8px-radius ball at 1080p
DEFAULT_MAX_AREA = 160_000    # px²

_OPEN_KERNEL_SIZE = 3         # morphological open kernel
_CLOSE_KERNEL_SIZE = 5        # morphological close kernel


def _process_frame(
    frame_bgr: np.ndarray,
    bg: np.ndarray,
    threshold: int,
    min_area: int,
    max_area: int,
    open_kernel: np.ndarray,
    close_kernel: np.ndarray,
    prev_frame: np.ndarray | None = None,
) -> list[dict]:
    """Detect foreground blobs in one frame.

    Combines background subtraction (|frame - bg|) with temporal differencing
    (|frame - prev_frame|) via OR, so that objects baked into the median
    background (e.g. a ball sitting at a position many times) are still
    detected when they move between consecutive frames.

    prev_frame must be at the same dimensions as bg if provided.
    Returns a list of detection dicts. Can be called without video I/O for testing.
    """
    bg_h, bg_w = bg.shape[:2]
    h, w = frame_bgr.shape[:2]
    if w != bg_w or h != bg_h:
        frame_bgr = cv2.resize(frame_bgr, (bg_w, bg_h), interpolation=cv2.INTER_AREA)

    # Background subtraction mask.
    diff_bg = cv2.absdiff(frame_bgr, bg)
    gray_bg = cv2.cvtColor(diff_bg, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray_bg, threshold, 255, cv2.THRESH_BINARY)

    # Temporal diff mask: OR with background mask to catch objects whose
    # appearance is captured in the median background plate.
    # Cloud motion between consecutive frames is small (≪ motion vs median),
    # so this also reduces spurious sky detections.
    if prev_frame is not None:
        diff_temp = cv2.absdiff(frame_bgr, prev_frame)
        gray_temp = cv2.cvtColor(diff_temp, cv2.COLOR_BGR2GRAY)
        _, mask_temp = cv2.threshold(gray_temp, threshold, 255, cv2.THRESH_BINARY)
        mask = cv2.bitwise_or(mask, mask_temp)

    # Morphological cleanup.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)

    # Connected components.
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask)

    detections: list[dict] = []
    for label in range(1, n_labels):  # skip background label 0
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue

        bx = int(stats[label, cv2.CC_STAT_LEFT])
        by = int(stats[label, cv2.CC_STAT_TOP])
        bw = int(stats[label, cv2.CC_STAT_WIDTH])
        bh = int(stats[label, cv2.CC_STAT_HEIGHT])

        # Fit ellipse (requires ≥ 5 contour points); fall back to circle.
        blob_mask = (labels == label).astype(np.uint8) * 255
        contours, _ = cv2.findContours(blob_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        if contours and len(contours[0]) >= 5:
            (cx, cy), (ax1_full, ax2_full), angle = cv2.fitEllipse(contours[0])
            # ax1_full is along the 'angle' direction; ax2_full is perpendicular.
            # Normalise so that a (semi-major) ≥ b (semi-minor), adjusting angle if needed.
            ax1_half = ax1_full / 2.0
            ax2_half = ax2_full / 2.0
            if ax1_half >= ax2_half:
                a, b = ax1_half, ax2_half
            else:
                a, b = ax2_half, ax1_half
                angle = (angle + 90.0) % 180.0
        else:
            r = math.sqrt(area / math.pi)
            cx = bx + bw / 2.0
            cy = by + bh / 2.0
            a = b = r
            angle = 0.0

        detections.append({
            "cx": round(float(cx), 1),
            "cy": round(float(cy), 1),
            "a": round(float(a), 1),
            "b": round(float(b), 1),
            "angle": round(float(angle), 1),
            "area": area,
            "bbox_x": bx,
            "bbox_y": by,
            "bbox_w": bw,
            "bbox_h": bh,
        })

    return detections


def detect_blobs(
    video_path: Path,
    bg: np.ndarray,
    in_time_s: float,
    out_time_s: float,
    fps: float,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: int = DEFAULT_MIN_AREA,
    max_area: int = DEFAULT_MAX_AREA,
    progress_callback=None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> dict[str, Any]:
    """Detect foreground blobs in every frame within [in_time_s, out_time_s].

    Returns a dict matching the detections.json schema.
    """
    import av  # type: ignore

    open_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_OPEN_KERNEL_SIZE,) * 2
    )
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_CLOSE_KERNEL_SIZE,) * 2
    )

    bg_h, bg_w = bg.shape[:2]
    frames: dict[str, list] = {}
    frame_count = 0
    detection_count = 0
    duration_s = max(out_time_s - in_time_s, 1.0)
    prev_bgr: np.ndarray | None = None  # previous decoded frame at bg dimensions

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "DEFAULT"

        # Seek to near the stable in point to avoid decoding the pre-stable portion.
        if in_time_s > 1.0:
            container.seek(int(in_time_s * 1_000_000))

        done = False
        for packet in container.demux(stream):
            for frame in packet.decode():
                pts = frame.pts if frame.pts is not None else frame.dts
                if pts is None:
                    continue
                ts = float(pts * stream.time_base)

                # Skip frames before stable bounds (seek may land on a keyframe earlier).
                if ts < in_time_s - 0.5:
                    continue
                # Stop after stable bounds.
                if ts > out_time_s + 0.5:
                    done = True
                    break

                frame_index = round(ts * fps)
                frame_count += 1

                if progress_callback is not None and frame_count % 300 == 0:
                    elapsed = ts - in_time_s
                    frac = progress_start + (progress_end - progress_start) * min(
                        elapsed / duration_s, 1.0
                    )
                    progress_callback(frac, f"Frame {frame_index} ({ts:.0f}s)")

                # Decode and resize to bg dimensions once; reuse as prev_frame next iteration.
                bgr = frame.to_ndarray(format="bgr24")
                h, w = bgr.shape[:2]
                if w != bg_w or h != bg_h:
                    bgr = cv2.resize(bgr, (bg_w, bg_h), interpolation=cv2.INTER_AREA)

                dets = _process_frame(
                    bgr, bg, threshold, min_area, max_area, open_kernel, close_kernel,
                    prev_frame=prev_bgr,
                )
                prev_bgr = bgr

                if dets:
                    frames[str(frame_index)] = dets
                    detection_count += len(dets)

            if done:
                break

    return {
        "fps": round(fps, 6),
        "bg_width": bg_w,
        "bg_height": bg_h,
        "frame_count": frame_count,
        "detection_count": detection_count,
        "threshold": threshold,
        "min_area": min_area,
        "max_area": max_area,
        "frames": frames,
    }
