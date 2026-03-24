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

import math

import cv2  # type: ignore
import numpy as np

from setup import Images


# ─── Tunable parameters ───────────────────────────────────────────────────────
#
# These were chosen by reasoning about the physical setup rather than by
# optimisation against the ground truth, so there is likely room for improvement.

# Minimum per-pixel intensity in the background-subtracted image (bsub) for a
# pixel to be considered "foreground". At 25/255 this filters sensor noise and
# mild JPEG artefacts while being sensitive to a fast-moving ball that may
# spend very few frames at any given position, making it appear only weakly in
# the median background.
_BSUB_THRESHOLD = 25

# Minimum per-pixel intensity in the temporal-difference images (prev, next)
# for a pixel to be considered "moving". Slightly lower than _BSUB_THRESHOLD
# because temporal diffs capture motion energy directly; even a slow ball
# produces a clear signal between adjacent frames.
_MOTION_THRESHOLD = 15

# Ball size bounds in pixels². At 1920×1080 the court is roughly 6.1 m wide,
# giving ~315 px/m at court level. A pickleball has radius ~37 mm, so its
# projected radius at court level is ~12 px and its area ~450 px². In the air
# and at camera angles the apparent size varies significantly, so we use a
# generous range. The upper bound is the main discriminator against players,
# whose blobs are easily 10× larger.
_MIN_BALL_AREA = 30     # pixels²
_MAX_BALL_AREA = 3000   # pixels²

# Minimum circularity (4π·area / perimeter²; 1.0 = perfect circle). The ball
# is the most circular moving object on the court. Players, shadows, and the
# net all produce elongated or irregular blobs. After morphological processing
# real ball blobs typically score 0.5–0.9; players score < 0.3.
_MIN_CIRCULARITY = 0.35


# ─── Detector ─────────────────────────────────────────────────────────────────

def find_ball(images: Images) -> tuple[float, float] | None:
    """Detect the ball using background subtraction combined with temporal motion.

    Strategy overview
    -----------------
    The ball is the smallest, most circular, fastest-moving object in the
    valid court volume. The detection pipeline has three stages:

      1. Build a binary mask of candidate pixels: pixels that differ from the
         median background AND are changing between adjacent frames.
      2. Extract connected-component blobs from that mask; discard any that are
         too large, too small, or too non-circular to be the ball.
      3. Score the remaining candidates and return the centroid of the best one.

    If no candidate passes all filters, return None (abstain).

    Args:
        images: named tuple with fields frame, bsub, prev, next — all BGR
                uint8 arrays of the same shape, masked to the court silhouette.

    Returns:
        (x, y) float coordinates of the predicted ball centre in background-
        plate pixel space, or None to abstain for this frame.
    """

    # ── Stage 1: Build a binary candidate mask ────────────────────────────────

    # Collapse each difference image from BGR to a single channel by taking the
    # per-pixel maximum across colour channels. This is more sensitive than
    # converting to grayscale (which averages channels) because the ball colour
    # may produce a strong response in only one channel (e.g. a yellow ball
    # saturates the green channel of bsub relative to a blue background sky).
    #
    # Alternative: convert to HSV and threshold the V channel, which is more
    # perceptually uniform and less sensitive to colour balance changes.
    gray_bsub = np.max(images.bsub, axis=2)
    gray_prev = np.max(images.prev, axis=2)
    gray_next = np.max(images.next, axis=2)

    # Hard threshold each channel to produce binary masks.
    _, mask_bsub = cv2.threshold(gray_bsub, _BSUB_THRESHOLD,  255, cv2.THRESH_BINARY)
    _, mask_prev = cv2.threshold(gray_prev, _MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
    _, mask_next = cv2.threshold(gray_next, _MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)

    # Require a pixel to be foreground in bsub AND moving in at least one
    # temporal direction. The AND removes static foreground objects (net posts,
    # court markings) that live in bsub but never move. The OR across prev/next
    # handles a ball momentarily stationary (e.g. at the peak of a lob) that
    # may appear in only one temporal diff.
    #
    # Alternative: replace the hard AND with a soft weighted sum
    #   combined = w_bsub * gray_bsub + w_motion * max(gray_prev, gray_next)
    # then threshold the result. This avoids missing a ball that narrowly fails
    # one threshold while clearly passing the other.
    mask_motion = cv2.bitwise_or(mask_prev, mask_next)
    mask = cv2.bitwise_and(mask_bsub, mask_motion)

    # ── Morphological cleanup ─────────────────────────────────────────────────

    # OPEN (erode → dilate) with a small elliptical kernel removes isolated
    # noise pixels and thin connections between adjacent blobs, helping to
    # separate a ball blob from nearby player blobs.
    open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_k, iterations=1)

    # CLOSE (dilate → erode) with a slightly larger kernel fills small gaps
    # inside the ball blob caused by the ball's hollow interior or specular
    # highlights, making the blob more solid and circular.
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k, iterations=2)

    # ── Stage 2: Extract and filter blobs ────────────────────────────────────

    # CHAIN_APPROX_NONE stores every contour point; needed for accurate
    # perimeter measurement used in the circularity calculation below.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    # ── Stage 3: Score candidates and pick the best ───────────────────────────

    # Alternative approach: cv2.HoughCircles on gray_bsub directly. It finds
    # circles without requiring a binary mask, and the radius constraint maps
    # naturally to the ball size. However, it is sensitive to its many tuning
    # parameters and tends to produce spurious detections on textured backgrounds.

    best_score = -1.0
    best_cx = best_cy = 0.0

    for cnt in contours:
        area = float(cv2.contourArea(cnt))

        # Size filter: discard blobs that are too small (noise) or too large
        # (players, shadows). This is the primary discriminator — the ball is
        # always the smallest meaningful moving object on the court.
        if area < _MIN_BALL_AREA or area > _MAX_BALL_AREA:
            continue

        perimeter = float(cv2.arcLength(cnt, True))
        if perimeter == 0:
            continue

        # Circularity: 1.0 for a perfect circle, lower for elongated shapes.
        # Players produce wide, irregular blobs; the ball produces a compact
        # near-circular blob. This is the secondary discriminator.
        circularity = 4.0 * math.pi * area / (perimeter * perimeter)
        if circularity < _MIN_CIRCULARITY:
            continue

        # Centroid via image moments.
        M = cv2.moments(cnt)
        if M["m00"] == 0:
            continue
        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]

        # Mean bsub intensity inside this blob: how strongly does this region
        # differ from the background? A real ball will be uniformly bright;
        # a marginal detection at the threshold boundary will be dim.
        blob_mask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(blob_mask, [cnt], -1, 255, cv2.FILLED)
        mean_bsub = float(cv2.mean(gray_bsub, mask=blob_mask)[0])

        # Mean motion intensity inside this blob: combines prev and next so
        # the score is high whether the ball is accelerating or decelerating.
        mean_motion = float(cv2.mean(
            np.maximum(gray_prev, gray_next), mask=blob_mask
        )[0])

        # Composite score: reward circularity, background contrast, and motion.
        # All three terms are normalised to [0, 1]. Circularity is weighted most
        # heavily because it is the hardest property for non-ball blobs to fake.
        #
        # Alternative scoring functions to explore:
        #   • Penalise area: prefer smaller blobs since the ball is smallest.
        #   • Add a colour term: the ball is typically yellow-green or white;
        #     compute HSV saturation of images.frame inside the blob and reward
        #     low saturation (white) or specific hue (yellow-green).
        #   • Use a Kalman filter to predict ball position from previous frames
        #     and reward proximity to the predicted location.
        score = (circularity           * 0.5
                 + mean_bsub  / 255.0  * 0.3
                 + mean_motion / 255.0 * 0.2)

        if score > best_score:
            best_score = score
            best_cx, best_cy = cx, cy

    if best_score < 0:
        # No blob survived all filters — abstain rather than guess.
        return None

    return (best_cx, best_cy)


if __name__ == "__main__":
    import setup
    rms = setup.calculate_metric(find_ball)
    print(f"RMS error: {rms:.2f} px  (ABSTAIN_ERROR = {setup.ABSTAIN_ERROR} px)")
