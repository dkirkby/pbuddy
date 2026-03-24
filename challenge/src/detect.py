"""
Ball detection for the PBuddy challenge.

find_ball() receives four images for a frame and returns the predicted ball
centre in bg-plate pixel coords, or None to abstain.
"""

from __future__ import annotations

from setup import Images


def find_ball(images: Images) -> tuple[float, float] | None:
    """Detect the ball in the given images and return (x, y), or None to abstain."""
    return None


if __name__ == "__main__":
    import setup
    rms = setup.calculate_metric(find_ball)
    print(f"RMS error: {rms:.2f} px  (ABSTAIN_ERROR = {setup.ABSTAIN_ERROR} px)")
