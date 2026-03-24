"""
Pickleball ball detection challenge — evaluation harness.

═══════════════════════════════════════════════════════════════════════════════
THE PROBLEM
═══════════════════════════════════════════════════════════════════════════════

A pickleball match was recorded from a fixed overhead-ish camera inside a
covered court (a tent). A human operator watched the video and manually
clicked on the ball in a set of selected frames, producing a set of
ground-truth ball positions. Your goal is to write a function that
automatically locates the ball in each frame.

═══════════════════════════════════════════════════════════════════════════════
THE IMAGES
═══════════════════════════════════════════════════════════════════════════════

For each annotated frame n, four BGR images are provided (OpenCV format,
dtype uint8):

  frame   The raw decoded video frame, scaled to the background-plate
          resolution (see COORDINATE SYSTEM below).

  bsub    Absolute difference between frame(n) and a pre-computed median
          background image built from many stable frames of the same video:
            bsub = |frame(n) - median_background|
          Pixels that look like the static background are dark; moving
          objects (including the ball) are bright.

  prev    Absolute difference between consecutive frames:
            prev = |frame(n) - frame(n-1)|
          Highlights pixels that changed since the previous frame.

  next    Absolute difference between consecutive frames:
            next = |frame(n+1) - frame(n)|
          Highlights pixels that will change in the next frame.

  The temporal images (prev, next) capture motion. A fast-moving ball
  typically appears as a bright blob in both. A ball that happens to
  be stationary for a moment may appear dimly in bsub but not in prev/next.

All four images are the same size and are registered to one another — a
pixel at (x, y) represents the same point in all four.

All pixels outside the valid court volume (a tent-shaped region that covers
the court plus a margin, up to net height at the centre and corner height at
the four corners) are set to black. Your detector can safely ignore any black
region at the image borders.

═══════════════════════════════════════════════════════════════════════════════
COORDINATE SYSTEM
═══════════════════════════════════════════════════════════════════════════════

All pixel coordinates use the standard image convention: origin (0, 0) at the
top-left corner, x increasing rightward, y increasing downward.

The "background-plate resolution" is the resolution of the median background
image, which equals the native video resolution capped at 1920×1080 with
aspect ratio preserved. All four images share this resolution, and all (x, y)
coordinates in truth.json are expressed in this same pixel space.

═══════════════════════════════════════════════════════════════════════════════
DATA LAYOUT
═══════════════════════════════════════════════════════════════════════════════

  data/truth.json                   — ground-truth ball positions
  data/images/frame_NNNNN.jpg       — raw video frame
  data/images/bsub_NNNNN.jpg        — background-subtracted frame
  data/images/prev_NNNNN.jpg        — temporal diff with previous frame
  data/images/next_NNNNN.jpg        — temporal diff with next frame

where NNNNN is the zero-padded frame index.

═══════════════════════════════════════════════════════════════════════════════
THE METRIC
═══════════════════════════════════════════════════════════════════════════════

The score is the RMS of per-frame pixel errors, where each frame contributes
one of two ways:

  Detection returned:  min(euclidean_distance, MAX_ERROR)² added to the sum.
  Abstention (None):   ABSTAIN_ERROR² added to the sum.

Because ABSTAIN_ERROR < MAX_ERROR, returning None is always preferable to
returning a wildly wrong position. A perfect detector scores 0; a pure
abstainer scores ABSTAIN_ERROR = 32 px.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, NamedTuple

import cv2  # type: ignore
import numpy as np

_DATA_DIR   = Path(__file__).parent.parent / "data"
_IMAGES_DIR = _DATA_DIR / "images"


# ─── Data types ───────────────────────────────────────────────────────────────

class Images(NamedTuple):
    """The four images available for a single annotated frame.

    All arrays are BGR uint8 (OpenCV format), same shape, same pixel space.
    Pixels outside the valid court tent silhouette are black in all four.
    """
    frame: np.ndarray  # raw decoded video frame
    bsub:  np.ndarray  # |frame(n) - median background|
    prev:  np.ndarray  # |frame(n) - frame(n-1)|
    next:  np.ndarray  # |frame(n+1) - frame(n)|


class Detection(NamedTuple):
    """One ground-truth ball position."""
    frame: int    # zero-based frame index in the original video
    x:     float  # ball centre x in background-plate pixel space
    y:     float  # ball centre y in background-plate pixel space


# ─── Loaders ──────────────────────────────────────────────────────────────────

def load_images(frame_n: int) -> Images:
    """Load and return the four images for frame *frame_n*.

    Images are loaded with cv2.imread and returned as BGR uint8 arrays.
    """
    n = f"{frame_n:05d}"
    return Images(
        frame=cv2.imread(str(_IMAGES_DIR / f"frame_{n}.jpg")),
        bsub =cv2.imread(str(_IMAGES_DIR / f"bsub_{n}.jpg")),
        prev =cv2.imread(str(_IMAGES_DIR / f"prev_{n}.jpg")),
        next =cv2.imread(str(_IMAGES_DIR / f"next_{n}.jpg")),
    )


def load_truth() -> list[Detection]:
    """Load and return all ground-truth detections, sorted by frame index."""
    with open(_DATA_DIR / "truth.json") as f:
        data = json.load(f)
    return [Detection(frame=d["frame"], x=d["x"], y=d["y"]) for d in data]


# ─── Metric ───────────────────────────────────────────────────────────────────

MAX_ERROR     = 64   # pixels — per-frame error is capped at this value
ABSTAIN_ERROR = 32   # pixels — cost of returning None instead of a detection;
                     #   less than MAX_ERROR so abstaining beats a bad detection


def calculate_metric(
    find_ball: Callable[[Images], tuple[float, float] | None],
) -> float:
    """Evaluate *find_ball* against the ground truth and return the RMS error.

    For every ground-truth frame, *find_ball* is called with an Images tuple.
    Its return value determines the per-frame squared-error contribution:

      (x, y) returned → min(euclidean_distance², MAX_ERROR²)
      None returned   → ABSTAIN_ERROR²
      Exception raised → treated as None (exception is printed)

    The RMS of all per-frame contributions is returned. A perfect detector
    scores 0; a pure abstainer scores ABSTAIN_ERROR px.

    Args:
        find_ball: callable accepting an Images namedtuple and returning
                   (x, y) in background-plate pixel space, or None to abstain.

    Returns:
        RMS error in pixels.

    Raises:
        ValueError: if truth.json is empty.
    """
    truth = load_truth()
    if not truth:
        raise ValueError("truth.json is empty — nothing to evaluate.")

    squared_errors: list[float] = []
    n_abstained = 0

    for det in truth:
        images = load_images(det.frame)
        try:
            result = find_ball(images)
        except Exception as exc:
            print(f"  frame {det.frame}: find_ball raised {type(exc).__name__}: {exc}")
            result = None

        if result is None:
            squared_errors.append(float(ABSTAIN_ERROR ** 2))
            n_abstained += 1
        else:
            dx = result[0] - det.x
            dy = result[1] - det.y
            squared_errors.append(min(dx * dx + dy * dy, float(MAX_ERROR ** 2)))

    if n_abstained:
        print(f"Abstained on {n_abstained}/{len(truth)} frames.")

    return math.sqrt(sum(squared_errors) / len(squared_errors))
