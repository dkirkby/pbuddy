"""Single-pass video scan: detect stable bounds and build median backgrounds together.

Decodes the video once sequentially:
  - Tracks frame-to-frame motion to find the stable (tripod-locked) period.
  - Captures BGR frames at equal frame-index intervals within each 2-minute chunk.
  - Computes one median background per overlapping 4-minute window (chunks k and k+1).
  - Stops decoding entirely once camera movement resumes after the stable period.

Window layout (relative to stable start):
  window 0: [0m, 4m]  (chunks 0 + 1)
  window 1: [2m, 6m]  (chunks 1 + 2)
  window 2: [4m, 8m]  (chunks 2 + 3)
  ...
Each chunk covers 2 minutes and fills exactly slots_per_chunk (150) frame slots.
At most 2 × slots_per_chunk frames are kept in memory at once.
Only windows backed by two complete chunks are saved; partial trailing data is discarded.
If the stable period is shorter than two full chunks, a single fallback median is returned.
"""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from pbva_core.types import StableBounds

# Maximum output resolution for the median background image.
# Native resolution is used up to this cap (aspect ratio preserved).
MAX_WORK_W = 1920
MAX_WORK_H = 1080

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
    chunk_duration_s: float = 120.0,
    slots_per_chunk: int = 150,
    progress_callback=None,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    max_width: int = MAX_WORK_W,
    max_height: int = MAX_WORK_H,
) -> tuple[StableBounds, list[np.ndarray], list[tuple[float, float]]]:
    """Detect stable video bounds and build median backgrounds in one video pass.

    Returns:
        (StableBounds, medians, window_times): stable in/out timestamps, a list of
        (H, W, 3) uint8 median background images (one per complete 4-minute window),
        and a parallel list of (start_s, end_s) video timestamps for each window.
        The lists always have length >= 1: if the stable period is shorter than two
        chunks a single fallback median covering the full stable period is returned.
    """
    import av  # type: ignore

    # Working resolution is determined after opening the stream.
    work_w: int = 0
    work_h: int = 0

    diff_buffer: deque = deque(maxlen=smoothing_window)
    prev_small: np.ndarray | None = None

    # State machine: pre_stable → stable → done
    state = 'pre_stable'
    stable_run   = 0
    unstable_run = 0
    in_time_s    = 0.0
    out_time_s   = duration_s
    last_stable_ts = 0.0

    # Chunk / window state (set once stable is detected).
    # buf[0] and buf[1] alternate: chunk k fills buf[k % 2].
    buf: list[list[np.ndarray | None]] = [
        [None] * slots_per_chunk,
        [None] * slots_per_chunk,
    ]
    chunk_frame_step: int = 1   # set after fps is known
    stable_start_frame: int = 0
    current_chunk: int = -1     # -1 = not yet in stable period
    next_slot: int = 0          # next slot to fill in current chunk
    next_target_frame: int = 0  # frame index of next target

    medians: list[np.ndarray] = []
    window_times: list[tuple[float, float]] = []

    frame_idx  = 0
    sample_idx = 0

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = 'DEFAULT'

        # Compute working resolution: native up to max, preserving aspect ratio.
        native_w = stream.width or max_width
        native_h = stream.height or max_height
        if native_w <= max_width and native_h <= max_height:
            work_w, work_h = native_w, native_h
        else:
            scale = min(max_width / native_w, max_height / native_h)
            work_w = round(native_w * scale)
            work_h = round(native_h * scale)

        # Derive fps for pre-computing chunk frame step.
        if stream.average_rate:
            fps = float(stream.average_rate)
        elif stream.guessed_rate:
            fps = float(stream.guessed_rate)
        else:
            fps = 30.0
        chunk_frame_step = max(1, round(chunk_duration_s * fps / slots_per_chunk))

        # If caller didn't supply a duration, read it from the container.
        if duration_s <= 0 and container.duration:
            duration_s = container.duration / 1_000_000

        for packet in container.demux(stream):
            for frame in packet.decode():
                is_sample = (frame_idx % sample_every_n_frames == 0)
                frame_idx += 1
                if not is_sample:
                    continue

                fi  = frame_idx - 1   # 0-based index of this frame
                ts  = float(frame.pts * stream.time_base)
                sample_idx += 1

                if progress_callback is not None and sample_idx % 30 == 0:
                    stable_s = ts - in_time_s if state == 'stable' else ts
                    if duration_s > 0:
                        ref_s = max(duration_s - in_time_s, stable_s)
                        scan_frac = min(stable_s / ref_s, 1.0) * _SCAN_FRAC
                    else:
                        # Duration unknown: use asymptotic formula so bar always advances.
                        # Approaches _SCAN_FRAC as stable_s → ∞, reaches 50% at one chunk.
                        scan_frac = stable_s / (stable_s + chunk_duration_s) * _SCAN_FRAC
                    frac = progress_start + (progress_end - progress_start) * scan_frac
                    label = f"Scanning {ts:.0f}s / {duration_s:.0f}s" if duration_s > 0 else f"Scanning {ts:.0f}s…"
                    progress_callback(frac, label)

                # Grayscale thumbnail for motion detection.
                gray = frame.to_ndarray(format='gray')
                h, w = gray.shape
                small = gray[::max(1, h // 180), ::max(1, w // 320)]

                if prev_small is not None and prev_small.shape == small.shape:
                    diff = float(np.median(np.abs(
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
                                stable_start_frame = fi
                                current_chunk = 0
                                next_slot = 0
                                next_target_frame = fi   # = stable_start + 0 * step
                        else:
                            stable_run = 0

                    elif state == 'stable':
                        if is_stable:
                            unstable_run = 0
                            last_stable_ts = ts

                            # Fill slots in the current chunk.  Decode BGR lazily so the
                            # resize only happens once even if this frame satisfies multiple
                            # consecutive targets (can occur when chunk_frame_step < sample
                            # _every_n_frames).
                            half = current_chunk % 2
                            frame_bgr: np.ndarray | None = None
                            while next_slot < slots_per_chunk and fi >= next_target_frame:
                                if frame_bgr is None:
                                    raw = frame.to_ndarray(format='bgr24')
                                    frame_bgr = cv2.resize(
                                        raw, (work_w, work_h), interpolation=cv2.INTER_AREA
                                    )
                                buf[half][next_slot] = frame_bgr
                                next_slot += 1
                                next_target_frame = (
                                    stable_start_frame
                                    + (current_chunk * slots_per_chunk + next_slot)
                                    * chunk_frame_step
                                )

                            # Advance chunk when all slots are filled.
                            if next_slot >= slots_per_chunk:
                                if current_chunk >= 1:
                                    # Window (current_chunk - 1) is now complete.
                                    w_idx  = current_chunk - 1
                                    if progress_callback is not None:
                                        # Use (w_idx+2)*chunk_duration_s — the stable time elapsed
                                        # when window w completes (2 chunks filled).  Consistent
                                        # with the scan-loop formula so the bar never steps back.
                                        completed_s = (w_idx + 2) * chunk_duration_s
                                        if duration_s > 0:
                                            scan_frac = min(completed_s / (duration_s - in_time_s), 1.0) * _SCAN_FRAC
                                        else:
                                            scan_frac = completed_s / (completed_s + chunk_duration_s) * _SCAN_FRAC
                                        frac = progress_start + (progress_end - progress_start) * scan_frac
                                        progress_callback(frac, f"Computing background {w_idx + 1}…")
                                    half_a = w_idx % 2
                                    half_b = current_chunk % 2
                                    stack  = np.stack(buf[half_a] + buf[half_b], axis=0)
                                    medians.append(np.median(stack, axis=0).astype(np.uint8))
                                    window_times.append((
                                        round(in_time_s + w_idx * chunk_duration_s, 2),
                                        round(in_time_s + (w_idx + 2) * chunk_duration_s, 2),
                                    ))

                                current_chunk += 1
                                new_half = current_chunk % 2
                                buf[new_half] = [None] * slots_per_chunk
                                next_slot = 0
                                next_target_frame = (
                                    stable_start_frame
                                    + current_chunk * slots_per_chunk * chunk_frame_step
                                )

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
        out_time_s = last_stable_ts
        if out_time_s - in_time_s < min_stable_s:
            in_time_s, out_time_s = 0.0, duration_s
    elif state == 'pre_stable':
        in_time_s, out_time_s = 0.0, duration_s

    bounds = StableBounds(in_time_s=round(in_time_s, 2), out_time_s=round(out_time_s, 2))

    # Fallback: fewer than two complete chunks → single median from all frames collected.
    if not medians:
        if progress_callback is not None:
            frac = progress_start + (progress_end - progress_start) * _SCAN_FRAC
            progress_callback(frac, "Computing background 1…")
        all_frames = [f for half in buf for f in half if f is not None]
        if not all_frames:
            raise RuntimeError(
                "No stable frames accumulated for background plate. "
                "Check that the video has a stationary camera period."
            )
        stack = np.stack(all_frames, axis=0)
        medians.append(np.median(stack, axis=0).astype(np.uint8))
        window_times.append((round(in_time_s, 2), round(out_time_s, 2)))

    return bounds, medians, window_times
