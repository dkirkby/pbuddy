"""Detect the stable (tripod-locked) portion of a pickleball video.

Uses mean absolute frame difference between sampled frames to find where
the camera finishes panning at the start and begins panning at the end.
"""

from __future__ import annotations

import numpy as np

from pbva_core.types import StableBounds


def detect_stable_bounds(
    video_path,
    duration_s: float,
    sample_every_n_frames: int = 15,
    motion_threshold: float = 6.0,
    smoothing_window: int = 5,
    min_stable_s: float = 30.0,
) -> StableBounds:
    """Return the stable in/out timestamps of the video.

    Args:
        video_path: Path to the video file.
        duration_s: Total video duration in seconds.
        sample_every_n_frames: Decode one frame per N frames.
        motion_threshold: Mean absolute pixel difference (0-255) above which
            the camera is considered moving.
        smoothing_window: Median smoothing window applied to the motion signal.
        min_stable_s: Minimum stable duration to accept; fall back to full
            video if no stable region is found.
    """
    import av  # type: ignore

    frames = []
    timestamps = []

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"  # initial quick scan

        # We need actual frame timestamps, so decode everything but skip
        # frames that aren't every Nth frame.
        frame_idx = 0
        # Reset to get all frames (not just keyframes).
        stream.codec_context.skip_frame = "DEFAULT"

        for packet in container.demux(stream):
            for frame in packet.decode():
                if frame_idx % sample_every_n_frames == 0:
                    # Convert to grayscale numpy array at reduced resolution.
                    img = frame.to_ndarray(format="gray")
                    # Downsample to 320×180 for speed.
                    h, w = img.shape
                    small = img[::h // 180, ::w // 320] if h > 180 else img
                    frames.append(small.astype(np.float32))
                    ts = float(frame.pts * stream.time_base)
                    timestamps.append(ts)
                frame_idx += 1

    if len(frames) < 3:
        return StableBounds(in_time_s=0.0, out_time_s=duration_s)

    # Compute frame-to-frame mean absolute difference.
    diffs = []
    for i in range(1, len(frames)):
        diff = np.mean(np.abs(frames[i] - frames[i - 1]))
        diffs.append(float(diff))
    diffs = np.array(diffs)

    # Smooth with rolling median.
    half = smoothing_window // 2
    smoothed = np.array([
        float(np.median(diffs[max(0, i - half): i + half + 1]))
        for i in range(len(diffs))
    ])

    is_stable = smoothed < motion_threshold

    # Find stable in-point: first index where is_stable stays True for >= 3 samples.
    in_idx = 0
    for i in range(len(is_stable) - 2):
        if is_stable[i] and is_stable[i + 1] and is_stable[i + 2]:
            in_idx = i
            break

    # Find stable out-point: last index where is_stable stays True for >= 3 samples.
    out_idx = len(is_stable) - 1
    for i in range(len(is_stable) - 1, 1, -1):
        if is_stable[i] and is_stable[i - 1] and is_stable[i - 2]:
            out_idx = i
            break

    # Convert sample indices to timestamps (diffs[i] is between frames[i] and frames[i+1]).
    in_time_s = float(timestamps[in_idx])
    out_time_s = float(timestamps[min(out_idx + 1, len(timestamps) - 1)])

    # Sanity check: stable range must be at least min_stable_s.
    if out_time_s - in_time_s < min_stable_s:
        return StableBounds(in_time_s=0.0, out_time_s=duration_s)

    return StableBounds(in_time_s=round(in_time_s, 2), out_time_s=round(out_time_s, 2))
