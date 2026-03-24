"""
Ball detection challenge utilities.

Data layout (relative to this file's grandparent directory):
  challenge/data/truth.json
  challenge/data/images/frame_NNNNN.jpg  — video frame at bg-plate resolution
  challenge/data/images/bsub_NNNNN.jpg   — |frame(n) - median bg|
  challenge/data/images/prev_NNNNN.jpg   — |frame(n) - frame(n-1)|
  challenge/data/images/next_NNNNN.jpg   — |frame(n+1) - frame(n)|

All images are masked to the tent silhouette (pixels outside are black).
All coordinates are in bg-plate pixel space.
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
    """Four images for a single annotated frame, all in bg-plate pixel space."""
    frame: np.ndarray  # decoded video frame
    bsub:  np.ndarray  # |frame(n) - median background|
    prev:  np.ndarray  # |frame(n) - frame(n-1)|
    next:  np.ndarray  # |frame(n+1) - frame(n)|


class Detection(NamedTuple):
    """Ground-truth ball position for one frame."""
    frame: int    # frame index
    x:     float  # ball centre x in bg-plate pixel space
    y:     float  # ball centre y in bg-plate pixel space


# ─── Loaders ──────────────────────────────────────────────────────────────────

def load_images(frame_n: int) -> Images:
    """Load the four images for frame *frame_n* and return them as an Images tuple."""
    n = f"{frame_n:05d}"
    return Images(
        frame=cv2.imread(str(_IMAGES_DIR / f"frame_{n}.jpg")),
        bsub =cv2.imread(str(_IMAGES_DIR / f"bsub_{n}.jpg")),
        prev =cv2.imread(str(_IMAGES_DIR / f"prev_{n}.jpg")),
        next =cv2.imread(str(_IMAGES_DIR / f"next_{n}.jpg")),
    )


def load_truth() -> list[Detection]:
    """Load truth.json and return a list of Detection entries sorted by frame."""
    with open(_DATA_DIR / "truth.json") as f:
        data = json.load(f)
    return [Detection(frame=d["frame"], x=d["x"], y=d["y"]) for d in data]


# ─── Metric ───────────────────────────────────────────────────────────────────

MAX_ERROR     = 64   # pixels — maximum per-frame error contribution
ABSTAIN_ERROR = 32   # pixels — penalty for not returning a detection (< MAX_ERROR,
                     #           so abstaining is always preferred to a bad detection)


def calculate_metric(
    find_ball: Callable[[Images], tuple[float, float] | None],
) -> float:
    """Compute RMS pixel error of *find_ball* against the ground-truth positions.

    Args:
        find_ball: function that receives an Images namedtuple and returns
                   (x, y) in bg-plate pixel coords, or None if the ball is
                   not detected.

    Returns:
        RMS error in pixels over all frames. Abstentions contribute ABSTAIN_ERROR
        and detections are capped at MAX_ERROR, so abstaining is always preferred
        to a bad detection.

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
