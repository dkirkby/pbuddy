# Ball Detection Challenge

## The Problem

A pickleball match was recorded from a fixed overhead-ish camera inside a covered court (a tent). A human operator watched the video and manually clicked on the ball in a set of selected frames, producing ground-truth ball positions. The goal is to write a function that automatically locates the ball in each frame.

## Input: Four Registered Images Per Frame

For each annotated frame *n*, four BGR images are provided (OpenCV format, `dtype uint8`):

| Name | Content |
|------|---------|
| `frame` | Raw decoded video frame |
| `bsub` | `\|frame(n) − median_background\|` — bright where objects differ from the static scene |
| `prev` | `\|frame(n) − frame(n−1)\|` — bright where pixels changed since the previous frame |
| `next` | `\|frame(n+1) − frame(n)\|` — bright where pixels will change in the next frame |

All four images are the same size and registered to one another — a pixel at `(x, y)` represents the same scene point in all four. Pixels outside the valid court volume (a tent-shaped silhouette covering the court up to net height at the centre and corner height at the four corners) are set to black and can be ignored.

## Coordinate System

Origin `(0, 0)` is the top-left corner, `x` increases rightward, `y` increases downward. All coordinates use the background-plate resolution, which equals the native video resolution capped at 1920×1080 with aspect ratio preserved.

## Data Layout

```
challenge/
├── data/
│   ├── truth.json                  — ground-truth ball positions [{frame, x, y}, ...]
│   └── images/
│       ├── frame_NNNNN.jpg         — raw video frame
│       ├── bsub_NNNNN.jpg          — background-subtracted frame
│       ├── prev_NNNNN.jpg          — temporal diff with previous frame
│       └── next_NNNNN.jpg          — temporal diff with next frame
└── src/
    ├── setup.py                    — Images/Detection types, data loaders, calculate_metric()
    └── detect.py                   — your find_ball() implementation (edit this)
```

## Scoring Metric

The score is the RMS of per-frame pixel errors. Each frame contributes:

- **Detection returned:** `min(euclidean_distance, MAX_ERROR)²`
- **Abstention (`None` returned):** `ABSTAIN_ERROR²`

| Constant | Value | Meaning |
|----------|-------|---------|
| `MAX_ERROR` | 64 px | Per-frame error cap |
| `ABSTAIN_ERROR` | 32 px | Cost of abstaining |

Because `ABSTAIN_ERROR < MAX_ERROR`, returning `None` is always preferable to returning a wildly wrong position. A perfect detector scores 0; a pure abstainer scores 32 px.

Frames are evaluated in a shuffled order so detectors cannot exploit temporal sequence.

## Your Task

Implement `find_ball(images: Images) -> tuple[float, float] | None` in `src/detect.py`. It receives an `Images` namedtuple and should return the ball centre as `(x, y)` in image pixel coordinates, or `None` to abstain.

## Environment Setup and Running

The `src/` directory contains its own `pyproject.toml` so that `uv` manages dependencies independently of the main PBuddy project. Set up the environment once:

```bash
cd challenge/src
uv sync
```

This creates a `.venv` inside `src/` and installs `numpy` and `opencv-python-headless`. After that, run the evaluation at any time with:

```bash
uv run detect.py
```

This prints the RMS error and the number of abstentions.
