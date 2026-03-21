"""Generate a median background plate from stable video frames.

Decodes N evenly-spaced frames across the stable interval, stacks them,
and computes the per-pixel temporal median to erase all moving objects.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


# Working resolution for background plate (width × height).
WORK_W = 960
WORK_H = 540


def build_background_plate(
    video_path,
    in_time_s: float,
    out_time_s: float,
    target_samples: int = 300,
    output_path: Path | None = None,
    progress_callback=None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> np.ndarray:
    """Return a (WORK_H, WORK_W, 3) uint8 median background image.

    Args:
        video_path: Path to video file.
        in_time_s: Start of stable interval (seconds).
        out_time_s: End of stable interval (seconds).
        target_samples: Number of frames to sample.
        output_path: If given, save the image as PNG to this path.
        progress_callback: Optional callable(fraction, message) called every 10 frames.
        progress_start: Fraction value at start of this stage (for scaling into overall progress).
        progress_end: Fraction value at end of this stage.
    """
    import av  # type: ignore

    duration = out_time_s - in_time_s
    if duration <= 0:
        raise ValueError(f"Invalid stable interval: {in_time_s}..{out_time_s}")

    # Evenly spaced target timestamps within the stable interval.
    sample_times = np.linspace(in_time_s, out_time_s, target_samples)

    frames: list[np.ndarray] = []

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        time_base = float(stream.time_base)

        for i, ts in enumerate(sample_times):
            if progress_callback is not None and i % 10 == 0:
                frac = progress_start + (progress_end - progress_start) * i / target_samples
                progress_callback(frac, f"Sampling frame {i}/{target_samples}")
            # Seek to the nearest keyframe before ts.
            pts_target = int(ts / time_base)
            try:
                container.seek(pts_target, stream=stream, backward=True, any_frame=False)
            except Exception:
                continue

            for packet in container.demux(stream):
                for frame in packet.decode():
                    frame_ts = float(frame.pts * stream.time_base)
                    if frame_ts >= ts - 0.5:  # accept frame within 0.5 s of target
                        img = frame.to_ndarray(format="bgr24")
                        resized = cv2.resize(img, (WORK_W, WORK_H), interpolation=cv2.INTER_AREA)
                        frames.append(resized)
                        break
                else:
                    continue
                break

    if not frames:
        raise RuntimeError("No frames could be decoded for background plate")

    stack = np.stack(frames, axis=0)  # (N, H, W, 3)
    median_bg = np.median(stack, axis=0).astype(np.uint8)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), median_bg)

    return median_bg
