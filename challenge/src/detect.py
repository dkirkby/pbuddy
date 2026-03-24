"""
Pickleball ball detection challenge — detector implementation.

═══════════════════════════════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════════════════════════════

Implement find_ball() below. It receives four registered BGR images for one
video frame and should return the ball centre as (x, y) in image pixel coords,
or None to abstain.

See setup.py for a full description of the problem, the images, the coordinate
system, and the scoring metric.

═══════════════════════════════════════════════════════════════════════════════
QUICK REFERENCE
═══════════════════════════════════════════════════════════════════════════════

images.frame  — raw video frame (BGR uint8)
images.bsub   — |frame(n) - median background| — bright where things moved
images.prev   — |frame(n) - frame(n-1)|        — bright where motion occurred
images.next   — |frame(n+1) - frame(n)|        — bright where motion will occur

Return (x, y) float in the same pixel space as the images, or None to abstain.
Abstaining costs ABSTAIN_ERROR = 32 px; a detection more than MAX_ERROR = 64 px
away from the truth costs the same as MAX_ERROR, so abstaining beats a bad guess.

═══════════════════════════════════════════════════════════════════════════════
RUNNING THE EVALUATION
═══════════════════════════════════════════════════════════════════════════════

From this directory:

    python detect.py

This calls calculate_metric(find_ball) from setup.py and prints the RMS error.
"""

from __future__ import annotations

from setup import Images


def find_ball(images: Images) -> tuple[float, float] | None:
    """Return the predicted ball centre (x, y) in image pixel coords, or None.

    Args:
        images: named tuple with fields frame, bsub, prev, next — all BGR
                uint8 arrays of the same shape, masked to the court silhouette.

    Returns:
        (x, y) float coordinates of the predicted ball centre in background-
        plate pixel space, or None to abstain for this frame.
    """
    return None  # stub — replace with your implementation


if __name__ == "__main__":
    import setup
    rms = setup.calculate_metric(find_ball)
    print(f"RMS error: {rms:.2f} px  (ABSTAIN_ERROR = {setup.ABSTAIN_ERROR} px)")
