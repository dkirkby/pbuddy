# PBuddy — Pickleball Video Analysis

A local web application for analyzing pickleball match video. It reconstructs ball trajectories from a single smartphone-quality camera recording and produces a highlight reel of all rallies with score overlays.

Processing runs as a 6-pass pipeline with human-in-the-loop review after each pass, so you can correct court geometry, ball color, player identities, and event annotations before the next stage runs.

**Status:** All 6 passes are implemented end-to-end.

## Requirements

- macOS, Linux, or Windows
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager
- [Node.js](https://nodejs.org/) 18+
- [ffmpeg](https://ffmpeg.org/) (must be on `PATH`; provides both `ffmpeg` and `ffprobe`)

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
2. **Pass 1 — Scene calibration** — detects stable video bounds, builds a median background image, and fits court geometry. Review by dragging the four corner handles onto the court corners; accept to lock in calibration.
3. **Pass 2 — Ball annotation** — scrub the video and click to mark ball positions frame by frame (center + radius). Also enter the four player names (serving team left/right and receiving team left/right), then mark each rally's start frame, stop frame, opening score, server, and receiver. Accept to save the full annotated dataset.
4. **Pass 3 — Color tagging** — samples RGB+HSV values from annotated ball pixels and generates hue-saturation and value-saturation scatter plots. Draw polygons on each plot to define the ball color region; accept to lock in the color model.
5. **Pass 4 — Ball detection** — scans every stable frame using motion, color, and court-silhouette masks to find ball candidates. Review the detection overlay on the video; accept to pass detections downstream.
6. **Pass 5 — Segment building** — groups Pass 4 detections into trajectory segments. Review segment polylines overlaid on the video; delete false-positive segments; accept to finalize.
7. **Pass 6 — Video export** — produces a highlight reel MP4 containing all rallies with cross-fade transitions, a score/player-name overlay, and a yellow ball-trail overlay. Also generates YouTube chapter timestamps you can paste directly into your video description. Download the export or accept to record it as the final output.

## Artifacts

All artifacts live under `data/projects/<project_id>/passes/pass{1-6}/`. Each pass has three subdirectories: `raw/` (pipeline output), `corrections/` (user edits), and `accepted/` (merged result used by downstream passes).

### Pass 1 — Scene Calibration

| File | Description |
|------|-------------|
| `raw/result.json` | Stable in/out frame bounds, frame dimensions, paths to median images, per-window time ranges |
| `raw/median_background_N.png` | Median background image(s), one per detected stable window |
| `accepted/result.json` | Same as raw result plus user-corrected court corner coordinates |
| `accepted/tent_mask.png` | Binary mask (white = inside court volume silhouette) used by Pass 4 |

### Pass 2 — Ball Annotation

| File | Description |
|------|-------------|
| `raw/result.json` | Video FPS and frame dimensions |
| `accepted/result.json` | FPS, frame dimensions, annotation count, ball radius statistics |
| `accepted/annotations.json` | Per-frame ball annotations: `{ "frameN": { x, y, radius } }` (browser frame numbers) |
| `accepted/rally.json` | Rally records (start\_frame, stop\_frame, score, serverName, receiverName, servingTeamWinsRally) and player\_names dict |
| `accepted/patches/raw/NNNNNN.png` | 64×64 px crop centred on each annotated ball |
| `accepted/patches/bg_sub/NNNNNN.png` | Same crop with median background subtracted |

### Pass 3 — Ball Color Tagging

| File | Description |
|------|-------------|
| `raw/ball_colors.csv` | R, G, B, H, S, V samples from annotated ball pixels |
| `raw/bg_colors.csv` | R, G, B, H, S, V samples from background pixels within the court silhouette |
| `raw/hue_saturation.png` | Hue vs Saturation scatter plot (ball pixels in true colour, background in grey) |
| `raw/hue_saturation.json` | Plot dimensions and axis value ranges |
| `raw/value_saturation.png` | Value vs Saturation scatter plot |
| `raw/value_saturation.json` | Plot dimensions and axis value ranges |
| `accepted/ball_color_polygons.json` | User-drawn filter polygons in hue-saturation and value-saturation spaces |

### Pass 4 — Ball Detection

| File | Description |
|------|-------------|
| `raw/detections.json` | Per-frame detections: frame, cx, cy, radius, area; also stable frame range and max radius |
| `raw/detections_map.png` | Binary image at background resolution showing all detection centres (white pixels) |
| `accepted/detections.json` | Copy of raw detections |

### Pass 5 — Segment Building

| File | Description |
|------|-------------|
| `raw/segments.json` | Segments with id, first\_frame, last\_frame, length, mean\_speed\_px\_per\_frame, and per-frame detections; plus algorithm parameters |
| `accepted/segments.json` | Same as raw with user-deleted segments removed |

### Pass 6 — Video Export

| File | Description |
|------|-------------|
| `raw/export.mp4` | Highlight reel: all rallies with cross-fades, score overlay, ball-trail overlay, embedded chapter markers, AAC audio |
| `raw/result.json` | Rally count, output duration (s), YouTube-pasteable chapter timestamp text |
| `raw/chapters.ffmeta` | FFMETADATA1 chapter definitions used during mux |
| `accepted/export.mp4` | Copy of raw export |
| `accepted/result.json` | Copy of raw result |

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
              ↘                          ↘
               ╰──────────────────────→ Pass 6
```

Each pass writes **raw** artifacts, waits for user review and optional **corrections**, then produces **accepted** artifacts that downstream passes depend on. See [`PIPELINE.md`](PIPELINE.md) for the pipeline design and [`ARCHITECTURE.md`](ARCHITECTURE.md) for implementation details.
