# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

Milestone 1 (Pass 1 end-to-end: upload → run → review → accept) is implemented. Key reference documents:

- `VISION.md` — requirements and accuracy targets
- `PIPELINE.md` — the 4-pass processing pipeline with user correction workflows
- `ARCHITECTURE.md` — detailed implementation blueprint

## Sport Dimensions

All court geometry and ball physical properties are defined in **`dimensions.json`** at the repo root. This is the single authoritative source — do not hardcode pickleball dimensions anywhere in the codebase. Key values (metric):

- Court: 13.41 m long × 6.10 m wide; non-volley zone 2.13 m from net
- Net: 0.91 m at posts, 0.86 m at centre; post-to-post width 6.71 m
- Ball diameter: 73–75 mm; weight: 22.1–26.5 g

## Technology Stack

**Backend:** Python with FastAPI + Uvicorn, managed via `uv` (`pyproject.toml` + `uv.lock`); monorepo workspace packages: `pbva-core`, `pbva-db`, `pbva-pipeline`, `pbva-api`, `pbva-worker`

**Frontend:** React + TypeScript + Vite, with TanStack Query, Zustand, Canvas/SVG overlays

**Persistence:** SQLite + SQLAlchemy 2.x

**Pipeline libraries:** NumPy, SciPy, OpenCV, PyAV, ffmpeg, Pydantic

## Commands

```bash
# First-time setup: install Python deps (uv manages its own Python via .python-version)
uv sync

# First-time frontend setup
cd apps/frontend && npm install

# Start the backend API server (also spawns the worker subprocess)
./scripts/dev_api.sh

# Start the frontend dev server (proxies /api and /ws to localhost:8000)
./scripts/dev_frontend.sh

# Start the worker subprocess independently (useful for debugging)
./scripts/dev_worker.sh

# Run backend tests (asyncio_mode = "auto" is configured globally)
uv run pytest

# Run a single test
uv run pytest tests/path/to/test_file.py::test_name

# Run the slow smoke test on test.mp4
uv run pytest tests/integration/test_pass1_smoke.py -m slow -v -s
```

## Architecture Overview

PBuddy is a **local web application** (no cloud dependency) for analyzing pickleball match videos via a 4-pass sequential pipeline:

```
Browser (React UI)
  ↕ HTTP REST + WebSocket
FastAPI Backend  ←→  SQLite (metadata)
  ↕
Worker Process  ←→  Filesystem (artifacts)
  ↕
Pass 1 → Pass 2 → Pass 3 → Pass 4
```

Each pass follows the **accepted-state pattern**:
1. Pass runs and writes **raw** artifacts
2. User reviews and submits **corrections** via the UI
3. User accepts; system merges raw + corrections into **accepted** artifacts
4. Next pass depends **only** on accepted artifacts — never on raw outputs

### The 4 Passes

| Pass | Goal | Key outputs |
|------|------|-------------|
| 1 | Global scene & camera calibration | Median background plate, court geometry, ball color profile |
| 2 | Temporal segmentation | "Live point" clips with dead time removed, initial match state |
| 3 | Player & ball event tracking | 2D ball positions, player tracks, hit/bounce/net events |
| 4 | 3D physics reconstruction | 3D trajectories, shot metrics, player analytics |

### Project Artifact Layout

```
data/projects/<project_id>/
├── uploads/original.mp4
├── derived/            # normalized video, thumbnails, audio
└── passes/
    └── pass{1-4}/
        ├── raw/        # system output
        ├── corrections/ # user-submitted corrections
        └── accepted/   # merged, used by downstream passes
```

### SQLite Schema (core tables)

- `projects` — project metadata, video info
- `passes` — per-project/pass state machine: `not_started → queued → running → produced_raw_output → waiting_for_user → accepted` (also `failed`, `cancelled`, `cancel_requested`)
- `jobs` — async job queue: `queued → running → succeeded/failed` (also `cancelled`, `cancel_requested`)
- `artifacts` — registry with roles: `raw`, `correction`, `accepted`, `preview`, `export`
- `events` — append-only log for WebSocket progress streaming

### Test Structure

Tests are organized in three tiers under `tests/`: `unit/` (fast, isolated), `integration/` (requires DB/filesystem), and `e2e/` (browser automation via Playwright). Slow tests are gated by the `slow` marker.

### API Pattern

REST for durable state; WebSocket (`/ws/projects/{id}`) for live progress. Core REST endpoints:

```
GET    /api/projects
POST   /api/projects
GET    /api/projects/{id}
POST   /api/projects/{id}/video
POST   /api/projects/{id}/passes/{pass}/run
PUT    /api/projects/{id}/passes/{pass}/corrections
POST   /api/projects/{id}/passes/{pass}/accept
```

### Frontend Pages

- `ProjectListPage` — list/create projects
- `ProjectHome` — project detail, pass state controls, workflow entry point
- `Pass1Page` — court geometry editor (SVG overlay on background plate)
- `Pass2Page` — ball annotation tool (frame scrubbing + point placement)

### Challenge Dataset

`challenge/` contains a standalone ball detection benchmark: 874 labelled frame images with `data/truth.json` ground truth annotations. `challenge/src/detect.py` is the detector under development; `challenge/src/setup.py` builds the dataset. Run with `uv run python challenge/src/detect.py`. Passes 3 and 4 are currently stubs.

## Frame Index Conventions

Frame indexing is a persistent source of off-by-one bugs. Follow these rules exactly.

### The PTS offset

The video file's first frame has PTS = 1/fps (not 0). This means:

- **Browser**: `requestVideoFrameCallback` gives `metadata.mediaTime` = actual PTS. For OpenCV frame N, `mediaTime ≈ (N+1)/fps`, so `Math.round(mediaTime * fps) = N+1`.
- **OpenCV**: sequential `cap.read()` after `cap.set(POS_FRAMES, in_frame)` gives `frame_idx = int(cap.get(CAP_PROP_POS_FRAMES)) - 1`. For the i-th read starting from `in_frame`, `frame_idx = in_frame + i`.
- **Net result**: browser `frameIndex` = OpenCV `frame_idx + 1` for the same physical frame.

### Rules

**In Python (pipeline):** Frame indices stored in JSON artifacts use OpenCV numbering (`frame_idx = int(cap.get(CAP_PROP_POS_FRAMES)) - 1`).

**In the frontend (VideoPlayer):** `frameIndex = Math.round(mediaTime * fps)` during playback (via rVFC). This is 1 higher than the OpenCV index for the same frame.

**When looking up Python artifacts by browser frame index**, subtract 1:
```ts
// ballDetections lookup in drawOverlay:
const fi = frameIndex - offset - 1   // -1 to convert browser→OpenCV numbering
```

**When looking up browser annotations by OpenCV frame index**, add 1:
```python
# pass2 annotations.json keys are browser frame numbers
ann_key = frame_idx + 1
if ann_key in ann_by_frame: ...
```

**When seeking the video from Python frame index N**, use `N / fps` — the browser will snap to the correct frame.

**Pass 2 annotation keys** are browser frame numbers (stored as strings). They were recorded using `Math.round(currentTime * fps)` after a seek. With the fixed step arithmetic (commit 8d650aa), seeks consistently land at `targetFrame / fps`, so annotation keys are consistently browser-numbered (= OpenCV + 1).

### Checklist for new frame-indexed code

- [ ] Is the frame number coming from OpenCV or from the browser?
- [ ] If crossing the Python↔browser boundary, apply the ±1 correction.
- [ ] File names for artifacts saved by Python and looked up by the browser should use the **browser frame number** (OpenCV + 1) so that labels and seeks work without adjustment.

## Key Design Principles

1. **Local-first** — all video, artifacts, and metadata stay on the user's machine
2. **Pass-oriented** — each pass is independently runnable, inspectable, and retryable
3. **Human-in-the-loop is first-class** — corrections are stored explicitly, not patched in memory
4. **Durable state** — app restarts must be recoverable; long-running compute never depends solely on in-memory state
5. **Simple deployment** — two processes (API server + worker); avoid Redis/Celery unless proven necessary
