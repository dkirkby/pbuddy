"""Single-pass video scan: detect stable bounds and build median background together.

Decodes the video once sequentially:
  - Tracks frame-to-frame motion to find the stable (tripod-locked) period.
  - Accumulates BGR frames via reservoir sampling once stability is confirmed.
  - Stops decoding entirely once camera movement resumes after the stable period.
"""

from __future__ import annotations

import random
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from pbva_core.types import StableBounds

# Output resolution for the median background image.
WORK_W = 960
WORK_H = 540

# Consecutive stable/unstable samples required for state transitions.
_STABLE_RUN  = 3
_UNSTABLE_RUN = 3

# Fraction of the progress range consumed by scanning vs. median computation.
_SCAN_FRAC = 0.95


def scan_video(
    video_path,
    duration_s: float,
    sample_every_n_frames: int = 15,
    motion_threshold: float = 6.0,
    smoothing_window: int = 5,
    min_stable_s: float = 30.0,
    target_samples: int = 300,
    output_path: Path | None = None,
    progress_callback=None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
) -> tuple[StableBounds, np.ndarray]:
    """Detect stable video bounds and build a median background in one video pass.

    Returns:
        (StableBounds, median_bg): stable in/out timestamps and a (WORK_H, WORK_W, 3)
        uint8 median background image computed from frames in the stable period.
    """
    import av  # type: ignore

    rng = random.Random(42)  # deterministic reservoir sampling

    diff_buffer: deque = deque(maxlen=smoothing_window)
    prev_small: np.ndarray | None = None

    # State machine: pre_stable → stable → done
    state = 'pre_stable'
    stable_run   = 0
    unstable_run = 0
    in_time_s    = 0.0
    out_time_s   = duration_s
    last_stable_ts = 0.0

    # Median frame buffer (reservoir sampling keeps up to target_samples frames).
    bg_frames: list[np.ndarray] = []
    n_stable_seen = 0  # total stable frames seen

    frame_idx  = 0
    sample_idx = 0

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = 'DEFAULT'

        for packet in container.demux(stream):
            for frame in packet.decode():
                is_sample = (frame_idx % sample_every_n_frames == 0)
                frame_idx += 1
                if not is_sample:
                    continue

                ts = float(frame.pts * stream.time_base)
                sample_idx += 1

                if progress_callback is not None and sample_idx % 30 == 0 and duration_s > 0:
                    scan_frac = min(ts / duration_s, 1.0) * _SCAN_FRAC
                    frac = progress_start + (progress_end - progress_start) * scan_frac
                    progress_callback(frac, f"Scanning {ts:.0f}s / {duration_s:.0f}s")

                # Grayscale thumbnail for motion detection.
                gray = frame.to_ndarray(format='gray')
                h, w = gray.shape
                small = gray[::max(1, h // 180), ::max(1, w // 320)]

                if prev_small is not None and prev_small.shape == small.shape:
                    diff = float(np.mean(np.abs(
                        small.astype(np.float32) - prev_small.astype(np.float32)
                    )))
                    diff_buffer.append(diff)
                    smoothed = float(np.median(diff_buffer))
                    is_stable = smoothed < motion_threshold

                    if state == 'pre_stable':
                        if is_stable:
                            stable_run += 1
                            if stable_run >= _STABLE_RUN:
                                state = 'stable'
                                in_time_s = ts
                                last_stable_ts = ts
                        else:
                            stable_run = 0

                    elif state == 'stable':
                        if is_stable:
                            unstable_run = 0
                            last_stable_ts = ts
                            # Accumulate BGR frame via reservoir sampling.
                            bgr = frame.to_ndarray(format='bgr24')
                            resized = cv2.resize(bgr, (WORK_W, WORK_H), interpolation=cv2.INTER_AREA)
                            n_stable_seen += 1
                            if len(bg_frames) < target_samples:
                                bg_frames.append(resized)
                            else:
                                j = rng.randint(0, n_stable_seen - 1)
                                if j < target_samples:
                                    bg_frames[j] = resized
                        else:
                            unstable_run += 1
                            if (unstable_run >= _UNSTABLE_RUN
                                    and last_stable_ts - in_time_s >= min_stable_s):
                                out_time_s = last_stable_ts
                                state = 'done'

                prev_small = small

                if state == 'done':
                    break

            if state == 'done':
                break

    # Finalise bounds.
    if state == 'stable':
        # Reached end of video while still stable — use what we have.
        out_time_s = last_stable_ts
        if out_time_s - in_time_s < min_stable_s:
            in_time_s, out_time_s = 0.0, duration_s
    elif state == 'pre_stable':
        # No stable region found — fall back to full video bounds.
        in_time_s, out_time_s = 0.0, duration_s

    bounds = StableBounds(in_time_s=round(in_time_s, 2), out_time_s=round(out_time_s, 2))

    if not bg_frames:
        raise RuntimeError(
            "No stable frames accumulated for background plate. "
            "Check that the video has a stationary camera period."
        )

    if progress_callback is not None:
        frac = progress_start + (progress_end - progress_start) * _SCAN_FRAC
        progress_callback(frac, "Computing median background…")

    stack = np.stack(bg_frames, axis=0)
    median_bg = np.median(stack, axis=0).astype(np.uint8)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), median_bg)

    return bounds, median_bg
