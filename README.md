# PBuddy — Pickleball Video Analysis

A local web application for analyzing pickleball match video. It reconstructs ball trajectories, detects key events (hits, bounces, net contacts), and tracks player positions from a single smartphone-quality camera recording.

Processing runs in a 4-pass pipeline with human-in-the-loop review after each pass, so you can correct court geometry, ball color, player identities, and event annotations before the next stage runs.

**Status:** Passes 1–5 are implemented. The pipeline covers scene calibration, ball annotation, color tagging, per-frame detection, and trajectory segment building.

## Requirements

- macOS, Linux, or Windows
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager
- [Node.js](https://nodejs.org/) 18+
- [ffmpeg](https://ffmpeg.org/) (provides `ffprobe`, used for video metadata)

## Installation

```bash
git clone https://github.com/dkirkby/pbuddy.git
cd pbuddy

# Install Python dependencies (uv downloads Python 3.12 automatically)
uv sync

# Install frontend dependencies
cd apps/frontend && npm install && cd ../..
```

## Running

PBuddy runs as two processes. Open two terminals from the repo root:

**Terminal 1 — API server** (also spawns the background worker):

macOS / Linux:
```bash
./scripts/dev_api.sh
```
Windows:
```powershell
uv run uvicorn pbva_api.main:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — Frontend dev server:**

macOS / Linux:
```bash
./scripts/dev_frontend.sh
```
Windows:
```powershell
cd apps\frontend && npm run dev
```

Then open [http://localhost:5173](http://localhost:5173) in your browser.

## Usage

Each pass follows the same pattern: run → review → accept. The next pass only starts after the previous one is accepted.

1. **Create a project** and upload a match video (MP4, MOV, etc.)
2. **Pass 1 — Scene calibration** — detects stable video bounds, builds a median background image, fits court geometry. Review by dragging corner handles; accept to lock in calibration.
3. **Pass 2 — Ball annotation** — presents the video for manual annotation of ball positions. Click frames to mark ball center and radius; accept to save the annotated dataset.
4. **Pass 3 — Color tagging** — samples RGB+HSV values from annotated ball pixels and generates scatter plots. Draw polygons on the hue-saturation and value-saturation plots to define the ball color region; accept to lock in the color model.
5. **Pass 4 — Ball detection** — scans every stable frame using motion, color, and court-silhouette masks to find ball candidates. Review the detection overlay on the video; accept to pass detections downstream.
6. **Pass 5 — Segment building** — groups Pass 4 detections into trajectory segments. Review segment polylines overlaid on the video; accept to finalize.

## Development

```bash
# Run the test suite
uv run pytest

# Run a single test file
uv run pytest tests/unit/pass1/test_detect_court.py -v

# Run the slow end-to-end smoke test (requires test.mp4 in repo root)
uv run pytest tests/integration/test_pass1_smoke.py -m slow -v -s
```

## Architecture

```
Browser (React + TypeScript)
  ↕  REST API + WebSocket
FastAPI backend  ←→  SQLite
  ↕
Worker process  ←→  Filesystem artifacts
  ↕
Pass 1 → Pass 2 → Pass 3 → Pass 4 → Pass 5
```

Each pass writes **raw** artifacts, waits for user review and optional **corrections**, then produces **accepted** artifacts that the next pass depends on. See [`PIPELINE.md`](PIPELINE.md) for the pipeline design and [`ARCHITECTURE.md`](ARCHITECTURE.md) for implementation details.
