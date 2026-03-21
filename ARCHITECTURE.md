# Pickleball Video Analysis System — Architecture Blueprint

This document is a detailed implementation blueprint for a local, browser-based pickleball video analysis application. It is written to guide an LLM coding agent and a human reviewer toward a consistent first implementation.

It translates the requirements from `PIPELINE.md` and `VISION.md` into a concrete software architecture.

---

## 1. Purpose

Build a **local web application** in which:

- The user opens a web browser and interacts with a local HTTP server.
- The backend is written in **Python**.
- The Python environment is **managed and reproducible**.
- The analysis pipeline runs in **strict sequential passes**.
- After each pass, the system pauses for **human-in-the-loop corrections**.
- The browser can inspect intermediate artifacts, submit corrections, and trigger the next pass.
- Heavy compute stays on the user's local machine.

The system is optimized for:

- single-camera smartphone video
- doubles play on a primary court
- real-world clutter, including adjacent courts and other balls
- user-guided correction when confidence is low
- robust, restartable, debuggable execution

---

## 2. Design Principles

1. **Local-first**
   - No cloud dependency for core processing.
   - All video, artifacts, and metadata remain on the user's machine.

2. **Pass-oriented architecture**
   - The pipeline is not a single monolithic function.
   - Each pass is independently runnable, inspectable, retryable, and versioned.

3. **Human-in-the-loop is a first-class feature**
   - User corrections are not ad hoc patches.
   - Corrections are stored explicitly and merged into an accepted state.

4. **Durable state over transient memory**
   - If the browser reloads or the app restarts, project state must be recoverable.
   - Long-running compute must never depend solely on in-memory state.

5. **Simple deployment**
   - Favor a small number of local processes.
   - Avoid distributed systems tooling such as Redis/Celery unless later proven necessary.

6. **LLM-agent friendly structure**
   - Clear module boundaries.
   - Strong typing.
   - Explicit schemas.
   - Minimal hidden coupling.

---

## 3. Recommended Technology Stack

### 3.1 Backend

**Recommendation:** FastAPI + Uvicorn

Why:

- Clean typed HTTP API
- Native WebSocket support
- Good integration with Pydantic and async I/O
- Easy local development and production serving

### 3.2 Python Environment

**Recommendation:** `uv` with `pyproject.toml` and committed `uv.lock`

Why:

- Reproducible managed environment
- Fast dependency resolution and install
- Standard Python packaging model

### 3.3 Frontend

**Recommendation:** React + TypeScript + Vite

Supporting libraries:

- React Router
- TanStack Query for server state
- Zustand for local UI/editor state
- Native WebSocket client for progress/events
- HTML5 video element or a thin video wrapper
- Canvas/SVG overlays for frame annotations

### 3.4 Metadata Store

**Recommendation:** SQLite + SQLAlchemy 2.x

Why:

- Single-user local app
- Good transactional semantics
- Simple backup/debugging
- No external DB server required

### 3.5 Artifact Storage

**Recommendation:** Filesystem-based project directories

Store large items on disk:

- uploaded video
- preview clips
- extracted frames
- median background images
- mask images
- NumPy arrays / parquet tables / JSON artifacts
- final exports

Store only metadata and file references in SQLite.

### 3.6 Worker Model

**Recommendation:** Separate local Python worker process with DB-backed job queue

Why:

- Safer for long-running heavy jobs
- Easier to cancel/retry
- Better crash isolation than in-process background tasks
- No need for Redis in initial version

### 3.7 Media / Vision Libraries

Recommended baseline choices:

- `ffmpeg` CLI for transcoding, probing, clip generation, thumbnails
- `PyAV` for frame-accurate decoding when needed
- `opencv-python` for image processing and geometry utilities
- `numpy`, `scipy` for numeric processing
- `pandas` or `polars` for tabular analytics
- `orjson` for fast JSON serialization
- `pydantic` for schemas

Optional ML stack, depending on implementation:

- `torch` for learned detection/tracking components
- `onnxruntime` for portable inference if model export is desirable

### 3.8 Testing

- `pytest`
- `pytest-asyncio`
- `httpx` for API tests
- Playwright for end-to-end browser tests

---

## 4. High-Level Runtime Topology

```text
Browser (React UI)
    |
    | HTTP + WebSocket
    v
FastAPI Server (API + static frontend + session management)
    |
    | SQLite metadata DB
    | Filesystem project/artifact store
    |
    +--> Worker Supervisor / Local Worker Process
             |
             +--> Pass 1
             +--> Pass 2
             +--> Pass 3
             +--> Pass 4
```

### Responsibilities

#### Browser

- Upload/select video
- Display artifacts from each pass
- Show live progress
- Allow corrections
- Trigger next pass explicitly
- Review final replay and exports

#### FastAPI server

- Serves the frontend
- Owns REST API and WebSocket endpoints
- Persists project metadata and job records
- Validates correction payloads
- Emits progress and state changes
- Launches or supervises worker process

#### Worker process

- Claims queued jobs from SQLite
- Executes pass logic
- Writes artifacts to disk
- Writes progress/events/status to DB
- Never talks directly to the browser

---

## 5. Why Jobs, Not Blocking HTTP Calls

A pass must **not** run inline inside a request handler while the browser waits for the response.

Instead:

1. Browser requests that a pass be run.
2. API creates a durable job record.
3. API returns immediately.
4. Worker claims and runs the job.
5. Worker writes progress and outputs.
6. Browser receives updates through WebSocket and/or polling.

This is required because passes may be long-running, GPU-heavy, and user-review gated.

---

## 6. Core Architectural Decision: Accepted State Per Pass

Every pass must distinguish among three categories of data:

1. **Raw output**
   - The algorithm's original result.

2. **User corrections**
   - User edits stored as an explicit patch or correction set.

3. **Accepted output**
   - The canonical result used by downstream passes.

### Example

For Pass 1:

- raw output: initial stabilization bounds, auto court geometry, inferred ball color profile
- user corrections: adjusted in/out bounds, dragged court corners, manual ball color override
- accepted output: final calibration package used by Pass 2 and Pass 3

### Rule

Downstream passes must depend only on **accepted** artifacts, never directly on raw output.

---

## 7. Recommended Repository Layout

```text
repo/
  pyproject.toml
  uv.lock
  README.md
  ARCHITECTURE.md

  apps/
    api/
      src/pbva_api/
        __init__.py
        main.py
        config.py
        lifespan.py
        dependencies.py
        logging.py
        websocket_manager.py
        routes/
          health.py
          projects.py
          jobs.py
          artifacts.py
          passes.py
          exports.py
        schemas/
          common.py
          project.py
          job.py
          pass1.py
          pass2.py
          pass3.py
          pass4.py
          correction.py
        services/
          project_service.py
          artifact_service.py
          job_service.py
          pass_service.py
          export_service.py

    worker/
      src/pbva_worker/
        __init__.py
        main.py
        worker_loop.py
        job_claiming.py
        progress.py
        execution_context.py

    frontend/
      package.json
      vite.config.ts
      src/
        main.tsx
        app/
          App.tsx
          routes.tsx
        api/
          client.ts
          ws.ts
        pages/
          ProjectHome.tsx
          Pass1Page.tsx
          Pass2Page.tsx
          Pass3Page.tsx
          Pass4Page.tsx
          ReplayPage.tsx
        components/
          VideoTimeline.tsx
          CourtEditor.tsx
          ColorProfileEditor.tsx
          ClipEditor.tsx
          EventTimeline.tsx
          BallEditor.tsx
          IdentitySwapTool.tsx
          ProgressPanel.tsx
          ArtifactViewer.tsx
        state/
          uiStore.ts
          editorStore.ts
        types/
          api.ts
          domain.ts

  packages/
    core/
      src/pbva_core/
        __init__.py
        config.py
        paths.py
        enums.py
        errors.py
        timecode.py
        media.py
        geometry.py
        court_model.py
        types.py
        hashing.py

    db/
      src/pbva_db/
        __init__.py
        engine.py
        models.py
        session.py
        migrations/

    pipeline/
      src/pbva_pipeline/
        __init__.py
        registry.py
        base.py
        contracts.py
        artifact_io.py
        accepted_state.py
        pass1/
          run.py
          detect_stable_bounds.py
          background_plate.py
          detect_court.py
          infer_ball_color.py
          schemas.py
        pass2/
          run.py
          segment_activity.py
          estimate_start_state.py
          schemas.py
        pass3/
          run.py
          track_players.py
          track_ball.py
          detect_events.py
          schemas.py
        pass4/
          run.py
          reconstruct_trajectory.py
          attribute_hits.py
          analytics.py
          schemas.py

  scripts/
    dev_api.sh
    dev_worker.sh
    dev_frontend.sh
    init_project.py

  data/
    projects/
      <project_id>/
        ... generated at runtime ...

  tests/
    unit/
    integration/
    e2e/
```

### Notes

- `packages/core` holds reusable domain primitives.
- `packages/db` isolates persistence concerns.
- `packages/pipeline` owns pass logic.
- `apps/api` and `apps/worker` are thin composition layers.
- This separation makes the codebase easier for an LLM agent to navigate.

---

## 8. Managed Python Environment

### 8.1 `pyproject.toml`

Use a single top-level `pyproject.toml` with optional dependency groups.

Recommended groups:

- `dev`
- `test`
- `cuda`
- `metal`

### 8.2 Example Dependency Intent

Core backend:

- fastapi
- uvicorn
- pydantic
- sqlalchemy
- alembic
- orjson
- numpy
- scipy
- opencv-python
- av
- ffmpeg-python or plain subprocess wrappers
- pyyaml

Dev/test:

- pytest
- pytest-asyncio
- httpx
- ruff
- mypy

### 8.3 Environment Rules

- Commit `uv.lock`.
- Pin major versions for critical libraries.
- Validate environment at startup with a diagnostics endpoint.
- Do not rely on global Python or Conda-only environments in the initial design.

---

## 9. Project Directory Layout on Disk

Each project gets its own directory.

```text
<data_root>/projects/<project_id>/
  project.json
  uploads/
    original.mp4
  derived/
    normalized.mp4
    audio.wav
    thumbnails/
  passes/
    pass1/
      jobs/
      raw/
        result.json
        median_background.png
        sampled_frames/
      corrections/
        latest.json
      accepted/
        result.json
    pass2/
      raw/
        result.json
        clips.csv
        previews/
      corrections/
        latest.json
      accepted/
        result.json
    pass3/
      raw/
        result.json
        tracks.parquet
        events.json
        overlays/
      corrections/
        latest.json
      accepted/
        result.json
    pass4/
      raw/
        result.json
        trajectory3d.parquet
        analytics.json
      corrections/
        latest.json
      accepted/
        result.json
  exports/
    final_events.csv
    final_trajectory.json
    annotated_replay.mp4
  cache/
    decoded_frames/
    feature_cache/
  logs/
    api.log
    worker.log
```

### Rules

- Raw outputs are immutable once written for a given job.
- Corrections are append-only or versioned.
- Accepted outputs are regenerated whenever corrections are accepted.
- All stored files should be content-addressable or job-versioned if overwrite risk exists.

---

## 10. SQLite Data Model

Use SQLite for metadata and workflow state.

### 10.1 Core Tables

#### `projects`

Fields:

- `id`
- `name`
- `status`
- `created_at`
- `updated_at`
- `root_path`
- `video_path`
- `video_duration_s`
- `video_fps`
- `video_width`
- `video_height`

#### `passes`

One row per project/pass combination.

Fields:

- `id`
- `project_id`
- `pass_name` (`pass1`, `pass2`, `pass3`, `pass4`)
- `state`
- `current_job_id`
- `latest_raw_artifact_id`
- `latest_correction_id`
- `latest_accepted_artifact_id`
- `updated_at`

#### `jobs`

Fields:

- `id`
- `project_id`
- `pass_name`
- `job_type` (`run_pass`, `rebuild_accepted_state`, `export`)
- `status` (`queued`, `running`, `succeeded`, `failed`, `cancelled`)
- `attempt`
- `claimed_by`
- `queued_at`
- `started_at`
- `finished_at`
- `error_message`
- `params_json`

#### `artifacts`

Fields:

- `id`
- `project_id`
- `pass_name`
- `artifact_role` (`raw`, `correction`, `accepted`, `preview`, `export`)
- `artifact_type` (`json`, `png`, `parquet`, `csv`, `mp4`, etc.)
- `path`
- `sha256`
- `created_at`
- `job_id`
- `summary_json`

#### `events`

Append-only operational events for progress and UI refresh.

Fields:

- `id`
- `project_id`
- `job_id`
- `event_type`
- `payload_json`
- `created_at`

### 10.2 Optional Tables Later

- `user_sessions`
- `annotations`
- `audit_log`
- `metrics`

---

## 11. State Machines

### 11.1 Project State

```text
created
  -> video_ready
  -> pass1_ready
  -> pass1_waiting_for_review
  -> pass1_accepted
  -> pass2_waiting_for_review
  -> pass2_accepted
  -> pass3_waiting_for_review
  -> pass3_accepted
  -> pass4_waiting_for_review
  -> pass4_accepted
  -> replay_ready
```

### 11.2 Pass State

```text
not_started
queued
running
produced_raw_output
waiting_for_user
accepted
failed
cancelled
```

### Transition rules

- Only one active `run_pass` job per project/pass.
- Pass `N+1` may run only if pass `N` is `accepted`.
- A correction submission does not automatically start the next pass.
- The user must explicitly trigger the next pass.

---

## 12. API Design

### 12.1 API Style

- JSON for structured responses
- Resource-oriented endpoints
- Stable schemas with typed payloads
- Binary/video/image artifacts served as files or streamed responses

### 12.2 Core Endpoints

#### Health and diagnostics

- `GET /api/health`
- `GET /api/diagnostics`

#### Projects

- `POST /api/projects`
- `GET /api/projects/{project_id}`
- `GET /api/projects`
- `DELETE /api/projects/{project_id}`

#### Video ingestion

- `POST /api/projects/{project_id}/video`
- `GET /api/projects/{project_id}/video/metadata`

#### Pass control

- `POST /api/projects/{project_id}/passes/pass1/run`
- `POST /api/projects/{project_id}/passes/pass2/run`
- `POST /api/projects/{project_id}/passes/pass3/run`
- `POST /api/projects/{project_id}/passes/pass4/run`

- `GET /api/projects/{project_id}/passes/{pass_name}`
- `GET /api/projects/{project_id}/passes/{pass_name}/artifacts`
- `POST /api/projects/{project_id}/passes/{pass_name}/accept`
- `POST /api/projects/{project_id}/passes/{pass_name}/retry`
- `POST /api/projects/{project_id}/passes/{pass_name}/cancel`

#### Corrections

- `PUT /api/projects/{project_id}/passes/pass1/corrections`
- `PUT /api/projects/{project_id}/passes/pass2/corrections`
- `PUT /api/projects/{project_id}/passes/pass3/corrections`
- `PUT /api/projects/{project_id}/passes/pass4/corrections`

#### Jobs

- `GET /api/projects/{project_id}/jobs`
- `GET /api/jobs/{job_id}`

#### Artifacts

- `GET /api/artifacts/{artifact_id}`
- `GET /api/projects/{project_id}/frames/{frame_index}`
- `GET /api/projects/{project_id}/clips/{clip_id}`

#### Exports

- `POST /api/projects/{project_id}/exports/final`
- `GET /api/projects/{project_id}/exports`

### 12.3 WebSocket

- `WS /ws/projects/{project_id}`

The browser subscribes once per open project.

Server pushes messages such as:

- `project_updated`
- `job_queued`
- `job_started`
- `job_progress`
- `artifact_ready`
- `pass_waiting_for_user`
- `pass_accepted`
- `job_failed`
- `export_ready`

### 12.4 Why Both REST and WebSocket

- REST is the source of truth and supports page reloads.
- WebSocket improves responsiveness and reduces polling.

---

## 13. API Schema Conventions

All API payloads should use Pydantic models.

### Common response envelope

```json
{
  "ok": true,
  "data": { ... },
  "error": null
}
```

### Job creation response

```json
{
  "ok": true,
  "data": {
    "job_id": "job_123",
    "project_id": "proj_001",
    "pass_name": "pass1",
    "status": "queued"
  },
  "error": null
}
```

### Progress event example

```json
{
  "type": "job_progress",
  "project_id": "proj_001",
  "job_id": "job_123",
  "pass_name": "pass3",
  "progress": 0.64,
  "stage": "track_ball",
  "message": "Processing clip 5/12"
}
```

---

## 14. Worker and Job Queue Design

### 14.1 Initial Recommendation

Implement a **custom DB-backed queue** instead of Celery/Redis.

Why:

- single-user local app
- one machine
- low operational complexity
- easier packaging

### 14.2 Worker loop

Pseudo-flow:

1. Poll `jobs` table for `queued` jobs.
2. Atomically claim one job.
3. Mark job `running`.
4. Execute pass code.
5. Periodically write progress events.
6. On success:
   - write artifacts
   - update pass state
   - mark job `succeeded`
7. On failure:
   - capture traceback
   - mark job `failed`
   - emit failure event

### 14.3 Claiming logic

Use a transaction to claim jobs safely.

Rules:

- claim oldest queued compatible job
- ensure only one running job per project
- optionally allow only one global heavy GPU job in the initial version

### 14.4 Cancellation

Jobs should be cancellation-aware.

- API marks a job as `cancel_requested`
- worker checks cancellation flag at safe checkpoints
- worker exits gracefully and marks `cancelled`

---

## 15. Pass Execution Contract

Every pass must implement the same contract.

### 15.1 Base interface

```python
class PipelinePass(Protocol):
    name: str

    def validate_inputs(self, ctx: PassContext) -> None: ...
    def load_inputs(self, ctx: PassContext) -> PassInputs: ...
    def run(self, ctx: PassContext, progress: ProgressReporter) -> RawPassResult: ...
    def write_raw_outputs(self, ctx: PassContext, result: RawPassResult) -> list[ArtifactRef]: ...
    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: RawPassResult,
        corrections: CorrectionPayload | None,
    ) -> AcceptedPassResult: ...
    def validate_corrections(self, payload: dict) -> CorrectionPayload: ...
```

### 15.2 `PassContext`

Must include:

- project metadata
- data root and paths
- DB session handle or service access
- hardware/runtime config
- accepted outputs from previous passes
- current job metadata

### 15.3 Raw vs accepted generation

Recommended flow:

- `run()` creates raw outputs.
- user submits corrections.
- `accept` endpoint invokes `build_accepted_output()`.
- accepted artifacts are written and registered.

This keeps computation and review phases distinct.

---

## 16. Pass-Specific Contracts

### 16.1 Pass 1 — Global Scene & Camera Calibration

#### Inputs

- uploaded video
- optional user-selected frame if needed later

#### Raw outputs

- stable in/out bounds
- sampled stable frames
- median background image
- candidate court detections
- primary court guess
- provisional calibration model
- broad ball color profile
- confidence summary

#### Corrections

- adjusted in/out times
- corrected court corners / net line
- selected primary court if ambiguous
- overridden ball color threshold or clicked ball sample

#### Accepted output

`pass1_accepted.json` should include:

- stable video bounds
- accepted court geometry
- accepted camera model
- accepted primary court ID
- accepted ball color model
- calibration metadata and confidence

### 16.2 Pass 2 — Temporal Segmentation

#### Inputs

- accepted pass 1 output
- stabilized video segment

#### Raw outputs

- candidate live-play intervals
- preview clips and thumbnails
- initial serving-side estimate per clip
- confidence scores

#### Corrections

- trim clip edges
- merge adjacent clips
- delete false clips
- add missed clip
- correct starting match state / serving state

#### Accepted output

`pass2_accepted.json` should include:

- canonical rally clip list
- rally ordering
- starting match state per rally
- clip metadata and confidence

### 16.3 Pass 3 — Player & Ball Event Tracking

#### Inputs

- accepted pass 1 output
- accepted pass 2 clip list

#### Raw outputs

- player tracks on primary court
- ball track in image coordinates
- event candidates: hits, bounces, net contacts
- confidence values and uncertainty windows
- overlay previews for review

#### Corrections

- add/delete/edit event markers
- correct ball boxes/centers on selected frames
- fix player track identity swaps when visible

#### Accepted output

`pass3_accepted.json` should include:

- accepted 2D player tracks
- accepted 2D ball trajectory
- accepted event timeline
- any identity-swap annotations propagated forward

### 16.4 Pass 4 — 3D Physics Reconstruction & Attribution

#### Inputs

- accepted pass 1 camera model
- accepted pass 2 rallies
- accepted pass 3 tracks and events

#### Raw outputs

- reconstructed 3D trajectory segments
- hit attribution candidates
- player positioning analytics
- shot metrics
- unforced error labels
- ambiguity flags

#### Corrections

- explicit hit attribution override
- explicit player identity swap correction at a frame/time boundary
- selective event correction if discovered late

#### Accepted output

`pass4_accepted.json` should include:

- final event timeline
- final player attribution
- final 3D trajectory samples
- derived metrics
- final replay overlays and export manifest

---

## 17. Frontend Architecture

### 17.1 Pages

- Project creation / upload page
- Project overview page
- Pass 1 review page
- Pass 2 review page
- Pass 3 review page
- Pass 4 review page
- Final replay / export page

### 17.2 State split

Use two categories of state:

1. **Server state** via TanStack Query
   - projects
   - pass status
   - artifacts
   - jobs
   - exports

2. **Editor state** via Zustand or component-local state
   - current selected frame
   - pending drag points
   - unsaved timeline edits
   - selected event marker
   - current overlay visibility

### 17.3 UI workflow rules

- User cannot run Pass 2 until Pass 1 is accepted.
- User cannot run Pass 3 until Pass 2 is accepted.
- User cannot run Pass 4 until Pass 3 is accepted.
- Every page should show:
  - current pass status
  - last run timestamp
  - confidence summary
  - unsaved correction warning
  - button to accept corrections and proceed

### 17.4 Core editor widgets

#### Pass 1

- timeline range selector
- median image viewer
- draggable court corners and net line
- color profile widget with sampled ball click

#### Pass 2

- timeline segmentation editor
- clip list panel
- rally state editor

#### Pass 3

- frame scrubber
- event timeline markers
- ball bounding-box editor
- player overlay toggle

#### Pass 4

- hit attribution resolver
- identity swap tool
- analytics tables/charts
- replay overlay controls

---

## 18. Artifact Access Pattern

The browser must retrieve artifacts through stable URLs.

### Recommendation

- Serve artifacts through API routes that validate project ownership and artifact existence.
- Internally map artifact IDs to filesystem paths.
- Avoid exposing raw filesystem paths directly to the frontend.

### Example

`GET /api/artifacts/art_123` could return:

- JSON directly for JSON artifacts
- file stream for PNG/MP4/CSV
- short metadata envelope with signed-like stable local URL if later needed

---

## 19. Eventing and Progress Reporting

### 19.1 ProgressReporter interface

Each pass should report:

- progress fraction in `[0, 1]`
- stage name
- user-readable message
- optional counters like clip index or frame range

### 19.2 Progress strategy

Use coarse but meaningful stages, for example in Pass 3:

- `prepare_inputs`
- `track_players`
- `track_ball`
- `detect_events`
- `write_outputs`

### 19.3 Event durability

Every progress update does not need permanent storage, but important milestones should be written to the `events` table:

- queued
- started
- raw output ready
- waiting for user
- accepted
- failed

---

## 20. Error Handling Strategy

### Principles

- Fail loudly in logs, clearly in the UI.
- Preserve partial outputs when safe.
- Never silently continue with invalid upstream artifacts.

### Error categories

- invalid input video
- missing accepted prior pass
- model/runtime not available
- pass computation failure
- schema validation failure
- corrupted artifact file

### UI behavior

On failure, show:

- pass name
- stage
- concise error summary
- retry action
- optional "download logs" action

---

## 21. Configuration System

Use layered configuration.

### Sources

- defaults in code
- `.env` file for local dev
- environment variables for overrides
- optional project-level config file later

### Key config fields

- data root
- DB path
- worker polling interval
- maximum concurrent jobs
- ffmpeg binary path
- hardware backend (`cpu`, `metal`, `cuda`)
- model paths
- logging level

Use a typed config object via Pydantic Settings.

---

## 22. Logging and Observability

### Logging

Use structured logs with fields:

- timestamp
- process (`api`, `worker`)
- project_id
- pass_name
- job_id
- stage
- level
- message

### Metrics later

Potential metrics:

- pass runtime
- clips processed per minute
- tracking continuity
- user correction counts

For the first implementation, logs and the `events` table are sufficient.

---

## 23. Security Model

This is a local app, but still avoid careless defaults.

### Recommendations

- Bind to `127.0.0.1` by default.
- Do not expose the server on all interfaces unless explicitly configured.
- Validate uploaded file types and paths.
- Prevent directory traversal when serving artifacts.
- Keep destructive actions explicit.

Authentication can be omitted in the first local-only version.

---

## 24. Suggested Request Flow by Pass

### Pass 1 flow

1. User uploads video.
2. API normalizes metadata and creates project.
3. User clicks `Run Pass 1`.
4. API enqueues pass 1 job.
5. Worker runs pass 1 and writes raw artifacts.
6. Worker marks pass 1 as `waiting_for_user`.
7. Browser loads median background and calibration artifacts.
8. User edits bounds/court/color.
9. Browser saves corrections.
10. User clicks `Accept Pass 1`.
11. API builds accepted pass 1 artifact.
12. UI enables `Run Pass 2`.

### Same pattern for Pass 2–4

Always:

- run
- inspect raw results
- save corrections
- accept canonical output
- enable next pass

---

## 25. Concrete Recommendation for Triggering Python Passes

This answers the key implementation question directly.

### Do this

- The browser issues a `POST` request to a pass-specific `/run` endpoint.
- The API inserts a job row into SQLite.
- The worker process claims the job and executes the Python pass module.
- The worker writes artifacts to disk and status/events to SQLite.
- The browser receives progress over WebSocket and fetches results by REST.

### Do not do this

- Do not have the browser wait on a blocking pass HTTP response.
- Do not keep pass outputs only in memory.
- Do not let downstream passes read unaccepted raw outputs.

---

## 26. Recommended Initial Scope of Implementation

### Phase 1

- project creation
- video upload
- SQLite metadata
- worker process and queue
- pass 1 run/review/accept flow
- pass 2 run/review/accept flow
- basic frontend shell

### Phase 2

- pass 3 tracking review tools
- frame-level ball correction
- WebSocket progress polish
- export system

### Phase 3

- pass 4 reconstruction and analytics
- richer replay viewer
- stronger diagnostics and test coverage

This phased approach reduces integration risk.

---

## 27. Coding Standards for the LLM Agent

1. Use Python 3.11+ typing everywhere.
2. Use Pydantic models for request/response and artifact schemas.
3. Keep API route handlers thin.
4. Put business logic in service modules.
5. Keep pass code pure where possible.
6. Make artifact writes explicit and testable.
7. Avoid hidden globals.
8. Add docstrings to public modules and classes.
9. Prefer small focused files over giant utility modules.
10. Never mix frontend-specific assumptions into pipeline code.

---

## 28. Minimum Schemas to Define Early

Create these schemas first:

- `ProjectSummary`
- `ProjectDetail`
- `JobSummary`
- `PassStatus`
- `ArtifactRef`
- `Pass1CorrectionPayload`
- `Pass2CorrectionPayload`
- `Pass3CorrectionPayload`
- `Pass4CorrectionPayload`
- `Pass1AcceptedOutput`
- `Pass2AcceptedOutput`
- `Pass3AcceptedOutput`
- `Pass4AcceptedOutput`

These become the backbone of API and storage consistency.

---

## 29. First End-to-End Milestone

The first meaningful milestone is:

> Upload one video, run Pass 1 as an asynchronous job, display the median background and detected court in the browser, submit corrections, accept the result, and persist everything so the project can be reopened later.

If this milestone is solid, the rest of the pass-oriented architecture will scale cleanly.

---

## 30. Final Recommendation Summary

Build the system as:

- **Frontend:** React + TypeScript + Vite
- **Backend:** FastAPI + Uvicorn
- **Worker:** separate local Python worker process
- **Environment:** `uv` + `pyproject.toml` + `uv.lock`
- **Metadata store:** SQLite + SQLAlchemy
- **Artifacts:** filesystem-based project directories
- **Transport:** REST for durable state + WebSocket for live progress
- **Workflow:** pass-oriented, sequential, user-reviewed, accepted-state-driven

This is the most robust and lowest-friction architecture for a local, interactive, human-in-the-loop sports video analysis system.

