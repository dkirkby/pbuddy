"""Infer a generous HSV color profile for the pickleball.

Samples frames from the stable interval, subtracts the median background to
find moving blobs, filters by expected ball size, and fits a bounding HSV
range to the candidate pixels.
"""

from __future__ import annotations

import numpy as np

from pbva_core.types import BallColorModel

# Expected pickleball diameter in pixels at working resolution (960x540).
# A ball ~6 cm at 4-8 m distance appears roughly 10-50 px in diameter.
BALL_MIN_AREA_PX = 40     # ~7 px diameter
BALL_MAX_AREA_PX = 2500   # ~56 px diameter


# Default optic yellow-green bounds if detection fails (pickleball standard colors).
_DEFAULT_LOWER = [20.0, 60.0, 120.0]   # H≈yellow-green, moderate saturation
_DEFAULT_UPPER = [80.0, 255.0, 255.0]


def infer_ball_color(
    video_path,
    median_bg: np.ndarray,
    in_time_s: float,
    out_time_s: float,
    n_samples: int = 50,
    percentile_margin: float = 10.0,
) -> tuple[BallColorModel, float]:
    """Return a generous BallColorModel and confidence score.

    Args:
        video_path: Path to the video.
        median_bg: The precomputed median background at working resolution.
        in_time_s: Stable interval start.
        out_time_s: Stable interval end.
        n_samples: Number of frames to sample.
        percentile_margin: Expand HSV bounds by this percentile on each side.
    """
    import av  # type: ignore
    import cv2

    sample_times = np.linspace(in_time_s + 5, out_time_s - 5, n_samples)
    WORK_W, WORK_H = median_bg.shape[1], median_bg.shape[0]

    all_hsv_pixels: list[np.ndarray] = []

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        time_base = float(stream.time_base)

        for ts in sample_times:
            pts_target = int(ts / time_base)
            try:
                container.seek(pts_target, stream=stream, backward=True, any_frame=False)
            except Exception:
                continue

            for packet in container.demux(stream):
                for frame in packet.decode():
                    frame_ts = float(frame.pts * stream.time_base)
                    if frame_ts >= ts - 0.5:
                        img = frame.to_ndarray(format="bgr24")
                        img_small = cv2.resize(img, (WORK_W, WORK_H), interpolation=cv2.INTER_AREA)

                        # Background subtraction.
                        diff = cv2.absdiff(img_small, median_bg)
                        diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                        _, fg_mask = cv2.threshold(diff_gray, 25, 255, cv2.THRESH_BINARY)

                        # Morphological cleanup.
                        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
                        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_DILATE, kernel)

                        # Find contours and filter by ball-sized area.
                        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        img_hsv = cv2.cvtColor(img_small, cv2.COLOR_BGR2HSV)

                        for cnt in contours:
                            area = cv2.contourArea(cnt)
                            if not (BALL_MIN_AREA_PX <= area <= BALL_MAX_AREA_PX):
                                continue
                            x, y, bw, bh = cv2.boundingRect(cnt)
                            aspect = bw / bh if bh > 0 else 0
                            if not (0.5 <= aspect <= 2.0):
                                continue  # not round enough
                            pixels = img_hsv[y:y + bh, x:x + bw].reshape(-1, 3)
                            all_hsv_pixels.append(pixels)
                        break
                else:
                    continue
                break

    if not all_hsv_pixels or sum(len(p) for p in all_hsv_pixels) < 20:
        return BallColorModel(hsv_lower=_DEFAULT_LOWER, hsv_upper=_DEFAULT_UPPER), 0.0

    all_pixels = np.vstack(all_hsv_pixels).astype(np.float32)

    lo = float(percentile_margin)
    hi = 100.0 - float(percentile_margin)

    hsv_lower = [
        float(np.percentile(all_pixels[:, 0], lo)),
        float(np.percentile(all_pixels[:, 1], lo)),
        float(np.percentile(all_pixels[:, 2], lo)),
    ]
    hsv_upper = [
        float(np.percentile(all_pixels[:, 0], hi)),
        float(np.percentile(all_pixels[:, 1], hi)),
        float(np.percentile(all_pixels[:, 2], hi)),
    ]

    # Ensure lower < upper with a minimum spread.
    MIN_SPREAD = [10.0, 30.0, 30.0]
    for i in range(3):
        if hsv_upper[i] - hsv_lower[i] < MIN_SPREAD[i]:
            mid = (hsv_lower[i] + hsv_upper[i]) / 2
            hsv_lower[i] = max(0, mid - MIN_SPREAD[i] / 2)
            hsv_upper[i] = min([180, 255, 255][i], mid + MIN_SPREAD[i] / 2)

    confidence = min(1.0, len(all_pixels) / 200.0)

    return (
        BallColorModel(
            hsv_lower=[round(v, 1) for v in hsv_lower],
            hsv_upper=[round(v, 1) for v in hsv_upper],
        ),
        round(confidence, 3),
    )
