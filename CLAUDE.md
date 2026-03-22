# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

Milestone 1 (Pass 1 end-to-end: upload → run → review → accept) is implemented. Key reference documents:

- `VISION.md` — requirements and accuracy targets
- `PIPELINE.md` — the 4-pass processing pipeline with user correction workflows
- `ARCHITECTURE.md` — detailed implementation blueprint

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
- `passes` — per-project/pass state machine (pending → running → awaiting_review → accepted)
- `jobs` — async job queue (queued → running → succeeded/failed)
- `artifacts` — registry with roles: raw, correction, accepted
- `events` — append-only log for WebSocket progress streaming

### Test Structure

Tests are organized in three tiers under `tests/`: `unit/` (fast, isolated), `integration/` (requires DB/filesystem), and `e2e/` (browser automation via Playwright). Slow tests are gated by the `slow` marker.

### API Pattern

REST for durable state; WebSocket (`/ws/projects/{id}`) for live progress. Core REST endpoints:

```
POST   /api/projects
POST   /api/projects/{id}/video
POST   /api/projects/{id}/passes/{pass}/run
PUT    /api/projects/{id}/passes/{pass}/corrections
POST   /api/projects/{id}/passes/{pass}/accept
```

## Key Design Principles

1. **Local-first** — all video, artifacts, and metadata stay on the user's machine
2. **Pass-oriented** — each pass is independently runnable, inspectable, and retryable
3. **Human-in-the-loop is first-class** — corrections are stored explicitly, not patched in memory
4. **Durable state** — app restarts must be recoverable; long-running compute never depends solely on in-memory state
5. **Simple deployment** — two processes (API server + worker); avoid Redis/Celery unless proven necessary
