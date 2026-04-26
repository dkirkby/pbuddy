# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

Pass 0 and Passes 2–6 are implemented end-to-end (run → review → accept). Pass 1 is implemented but will be reworked to consume Pass 0's accepted output (court corners + K1) rather than recomputing its own court geometry. Key reference documents:

- `VISION.md` — requirements and accuracy targets
- `PIPELINE.md` — the processing pipeline with user correction workflows
- `ARCHITECTURE.md` — detailed implementation blueprint

## Sport Dimensions

All court geometry and ball physical properties are defined in **`dimensions.json`** at the repo root. This is the single authoritative source — do not hardcode pickleball dimensions anywhere in the codebase. Key values (metric):

- Court: 13.41 m long × 6.10 m wide; non-volley zone 2.13 m from net
- Net: 0.91 m at posts, 0.86 m at centre; post-to-post width 6.71 m
- Ball diameter: 73–75 mm; weight: 22.1–26.5 g

## Technology Stack

**Backend:** Python with FastAPI + Uvicorn, managed via `uv` (`pyproject.toml` + `uv.lock`); monorepo workspace — `packages/` holds pure libraries (`pbva-core`, `pbva-db`, `pbva-pipeline`), `apps/` holds executables (`pbva-api`, `pbva-worker`, `frontend`)

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

# Lint and format (ruff is configured in pyproject.toml, line-length=100, target py312)
uv run ruff check .
uv run ruff format .

# Type-check
uv run mypy packages/ apps/
```

## Architecture Overview

PBuddy is a **local web application** (no cloud dependency) for analyzing pickleball match videos via a 7-pass sequential pipeline:

```
Browser (React UI)
  ↕ HTTP REST + WebSocket
FastAPI Backend  ←→  SQLite (metadata)
  ↕
Worker Process  ←→  Filesystem (artifacts)
  ↕
Pass 0 → Pass 1 → Pass 2 → Pass 3 → Pass 4 → Pass 5
                       ↘                          ↘
                        ╰──────────────────────→ Pass 6
```

Each pass follows the **accepted-state pattern**:
1. Pass runs and writes **raw** artifacts
2. User reviews and submits **corrections** via the UI
3. User accepts; system merges raw + corrections into **accepted** artifacts
4. Next pass depends **only** on accepted artifacts — never on raw outputs

### The 7 Passes

| Pass | Goal | Key outputs |
|------|------|-------------|
| 0 | Identify Court and Specify Camera Model | Median background (30 frames at midpoint, stride 15), 4 court corner positions (¼-pixel precision), radial distortion K1 (single-term division model: r_u = r_d / (1 + K1·r_d²)) |
| 1 | Identify Background and Court Outline | Median background plate(s), court geometry, stable time bounds *(will be reworked to consume Pass 0 output)* |
| 2 | Rally and Ball Annotation | Per-frame ball position + radius annotations, patch images |
| 3 | Ball color tagging | RGB+HSV pixel samples, hue-saturation & value-saturation scatter plots |
| 4 | Ball detection | Per-frame motion+color+silhouette candidate detections across stable range |
| 5 | Segment building | Trajectory segments grouped from Pass 4 detections; filtered by min length (5) and min mean speed (5 px/fr); each segment carries `first_frame`, `last_frame`, `length`, `mean_speed_px_per_frame`, `detections`; user can delete segments before accepting |
| 6 | Video export | Highlight reel: rally segments from Pass 2 encoded frame-accurately via PyAV with cross-fade transitions (median background), score/player-name overlay, yellow ball-trail overlay from Pass 5 segments (1 s trailing window, opacity 5%→80%, lineWidth 1→6 px), sample-accurate audio splice with fades, MP4 chapter markers, and YouTube-pasteable chapter timestamps; no user corrections needed |

Pass 6 requires `pass2/accepted/rally.json` and optionally reads `pass5/accepted/segments.json` for the ball-trail overlay (trail is silently skipped if absent). Re-running Pass 1 or Pass 2 cascades to invalidate Pass 6.

Pass 0 accepted output (`pass0/accepted/result.json`) stores `court_geometry` (4 corner pixel coords at ¼-pixel resolution), `k1` (distortion coefficient), `bg_width`, and `bg_height`. Pass 0 currently has no downstream dependents in `pipeline_schema.json`; that wiring will be added when Pass 1 is reworked.

### Project Artifact Layout

```
data/projects/<project_id>/
├── uploads/original.mp4
├── derived/            # normalized video, thumbnails, audio
└── passes/
    └── pass{0-6}/
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
- `Pass0Page` — court corner alignment and camera model: median image with red SVG court overlay (curves with K1 distortion), K1 slider (−0.5 to +0.5), 2×2 grid of 4× zoom boxes (one per corner) for ¼-pixel precision dragging via `movementX/Y` document-level listeners
- `Pass1Page` — court geometry editor (SVG overlay on background plate) *(will be reworked)*
- `Pass2Page` — ball annotation tool (frame scrubbing + point placement)
- `Pass3Page` — ball color polygon editor (SVG overlays on hue-saturation and value-saturation scatter plots)
- `Pass4Page` — detection reviewer (video player with per-frame ball detection overlay)
- `Pass5Page` — segment reviewer (video player with detection + segment polyline overlays)
- `Pass6Page` — export reviewer (rally table with output timestamps, download link, copyable YouTube chapter timestamps, accept button)

### Monorepo Package Roles

- **`pbva-core`** (`packages/core`) — shared primitives: `Settings` (config), `PassPaths` (artifact path builder), `enums` (pass states, job states), `types`, `errors` (`WorkerCancelled`), `dimensions` (loads `dimensions.json`)
- **`pbva-db`** (`packages/db`) — SQLAlchemy models and DB engine; single source of truth for the schema
- **`pbva-pipeline`** (`packages/pipeline`) — one subpackage per pass (`pass0/`…`pass6/`), each with `run.py`; `base.py` defines `PassPaths`, `ProgressReporter` protocol, and `NullProgress`
- **`pbva-api`** (`apps/api`) — FastAPI routes (`routes/projects.py`, `passes.py`, `artifacts.py`, `jobs.py`) + `websocket_manager.py` for broadcasting events
- **`pbva-worker`** (`apps/worker`) — `worker_loop.py` polls the job queue; `job_claiming.py` atomically claims a job; `execution_context.py` wraps pass execution with progress reporting and cancellation; `progress.py` writes events to the DB for WebSocket streaming

**`pipeline_schema.json`** at the repo root is the authoritative artifact dependency graph. It lists every raw artifact produced per pass, which upstream artifacts and user settings each depends on, and which user-editable settings exist per pass. The dirty-flag invalidation logic reads this file to determine which downstream artifacts must be re-run when a pass is re-run or corrections change.

### Frontend Module Roles

- `src/api/client.ts` — typed fetch wrappers for all REST endpoints; `src/api/ws.ts` — WebSocket subscription hook
- `src/state/editorStore.ts` — Zustand store shared across pass editor pages (correction data, dirty flags)
- `src/components/VideoPlayer.tsx` — reusable player with `requestVideoFrameCallback` loop and overlay canvas; `CourtOverlay.tsx` — SVG court geometry editor
- `src/lib/courtCamera.ts` — court↔camera homography math; `src/lib/dimensions.ts` — JS-side court dimensions loaded from `dimensions.json`; `src/lib/PickleballDoublesGame.ts` — game state helpers

### Challenge Dataset

`challenge/` contains a standalone ball detection benchmark: 874 labelled frame images with `data/truth.json` ground truth annotations. `challenge/src/detect.py` is the detector under development; `challenge/src/setup.py` builds the dataset. Run with `uv run python challenge/src/detect.py`.

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

**Pass 2 annotation keys** are browser frame numbers stored by `handleContainerClick` in VideoPlayer. The click handler must use `lastFrameIndexRef.current` (the frameIndex last set by `drawOverlay`) rather than recomputing from `video.currentTime`. After playback, rVFC sets `frameIndex = N+1` for displayed frame N, but `video.currentTime` still reflects PTS N/fps — so recomputing from `currentTime` gives N while the counter shows N+1, causing a silent off-by-one in the stored annotation key.

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
