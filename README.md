# PBuddy — Pickleball Video Analysis

A local web application for analyzing pickleball match video. It reconstructs ball trajectories, detects key events (hits, bounces, net contacts), and tracks player positions from a single smartphone-quality camera recording.

Processing runs in a 4-pass pipeline with human-in-the-loop review after each pass, so you can correct court geometry, ball color, player identities, and event annotations before the next stage runs.

**Status:** Pass 1 (scene calibration) is implemented. Passes 2–4 are in development.

## Requirements

- macOS (Apple Silicon) or Linux
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
```bash
./scripts/dev_api.sh
```

**Terminal 2 — Frontend dev server:**
```bash
./scripts/dev_frontend.sh
```

Then open [http://localhost:5173](http://localhost:5173) in your browser.

## Usage

1. **Create a project** and upload a match video (MP4, MOV, etc.)
2. **Run Pass 1** — the system detects stable video bounds, builds a median background image, fits court geometry, and profiles the ball color
3. **Review Pass 1** — inspect the detected court overlay; drag the handles to correct corner positions if needed; check the ball color and stable time bounds
4. **Accept Pass 1** — locks in the calibration for use by downstream passes

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
Pass 1 → Pass 2 → Pass 3 → Pass 4
```

Each pass writes **raw** artifacts, waits for user review and optional **corrections**, then produces **accepted** artifacts that the next pass depends on. See [`PIPELINE.md`](PIPELINE.md) for the full pipeline design and [`ARCHITECTURE.md`](ARCHITECTURE.md) for implementation details.
