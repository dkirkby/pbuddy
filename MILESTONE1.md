# Milestone 1 — First End-to-End Pass

**Goal (from ARCHITECTURE.md §29):**
> Upload one video, run Pass 1 as an asynchronous job, display the median background and detected court in the browser, submit corrections, accept the result, and persist everything so the project can be reopened later.

**Test video:** `test.mp4` → 1920×1080, 30 fps, H.264, ~19 min (1147 s, ~34,400 frames)

---

## Overview of Steps

```
Step 1  Repository & environment scaffolding
Step 2  Core domain primitives  (packages/core)
Step 3  Persistence layer       (packages/db)
Step 4  Pass 1 pipeline code    (packages/pipeline/pass1)
Step 5  Worker process          (apps/worker)
Step 6  API backend             (apps/api)
Step 7  Frontend                (apps/frontend)
Step 8  Integration & validation
```

Each step has a **Done when** check that can be verified before proceeding.

---

## Step 1 — Repository & Environment

### 1.1 Python environment

Create `pyproject.toml` at repo root with `uv`.

```toml
[project]
name = "pbuddy"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "orjson>=3.10",
    "numpy>=1.26",
    "scipy>=1.13",
    "opencv-python>=4.9",
    "av>=12.0",
    "ffmpeg-python>=0.2",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27", "ruff>=0.4", "mypy>=1.10"]

[tool.uv.sources]
# local package paths registered here in Step 2
```

Register the three packages as editable sources:

```toml
[tool.uv.workspace]
members = ["packages/core", "packages/db", "packages/pipeline", "apps/api", "apps/worker"]
```

Run `uv sync` and commit `uv.lock`.

### 1.2 Directory skeleton

Create empty `__init__.py` files and the directory tree from ARCHITECTURE.md §7 for all packages and apps used in this milestone:

```
packages/core/src/pbva_core/
packages/db/src/pbva_db/
packages/pipeline/src/pbva_pipeline/pass1/
apps/api/src/pbva_api/routes/
apps/api/src/pbva_api/schemas/
apps/api/src/pbva_api/services/
apps/worker/src/pbva_worker/
apps/frontend/src/
data/projects/       ← gitignored at runtime
tests/unit/
tests/integration/
```

Each sub-package gets its own minimal `pyproject.toml` so `uv` can register it.

### 1.3 Configuration

Create `packages/core/src/pbva_core/config.py` using `pydantic-settings`:

```python
class Settings(BaseSettings):
    data_root: Path = Path("data")
    db_path: Path = Path("data/pbuddy.db")
    worker_poll_interval_s: float = 2.0
    max_concurrent_jobs: int = 1
    ffmpeg_bin: str = "ffmpeg"
    hardware_backend: Literal["cpu", "metal", "cuda"] = "cpu"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PBUDDY_")
```

Create `.env` at repo root (gitignored):

```
PBUDDY_DATA_ROOT=data
PBUDDY_HARDWARE_BACKEND=metal   # Apple Silicon
```

### 1.4 Development scripts

Create `scripts/dev_api.sh`, `scripts/dev_worker.sh`, `scripts/dev_frontend.sh`:

```bash
# dev_api.sh
uv run uvicorn pbva_api.main:app --reload --host 127.0.0.1 --port 8000

# dev_worker.sh
uv run python -m pbva_worker.main

# dev_frontend.sh
cd apps/frontend && npm run dev
```

**Done when:** `uv sync` runs without errors; `uv run python -c "import pbva_core"` succeeds.

---

## Step 2 — Core Domain Primitives (`packages/core`)

### 2.1 Enums (`enums.py`)

```python
class ProjectStatus(str, Enum):
    created = "created"
    video_ready = "video_ready"
    pass1_ready = "pass1_ready"
    pass1_waiting_for_review = "pass1_waiting_for_review"
    pass1_accepted = "pass1_accepted"
    # ... extend for later passes

class PassState(str, Enum):
    not_started = "not_started"
    queued = "queued"
    running = "running"
    produced_raw_output = "produced_raw_output"
    waiting_for_user = "waiting_for_user"
    accepted = "accepted"
    failed = "failed"
    cancelled = "cancelled"

class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    cancel_requested = "cancel_requested"

class ArtifactRole(str, Enum):
    raw = "raw"
    correction = "correction"
    accepted = "accepted"
    preview = "preview"
```

### 2.2 Path helpers (`paths.py`)

```python
def project_root(data_root: Path, project_id: str) -> Path
def uploads_dir(data_root: Path, project_id: str) -> Path
def derived_dir(data_root: Path, project_id: str) -> Path
def pass_dir(data_root: Path, project_id: str, pass_name: str) -> Path
def pass_raw_dir(...)
def pass_corrections_dir(...)
def pass_accepted_dir(...)
```

### 2.3 Errors (`errors.py`)

```python
class PBuddyError(Exception): ...
class ProjectNotFound(PBuddyError): ...
class PassNotReady(PBuddyError): ...
class ArtifactMissing(PBuddyError): ...
class InvalidCorrection(PBuddyError): ...
```

### 2.4 Pydantic API schemas (`types.py`)

Define the schemas listed in ARCHITECTURE.md §28:

- `ProjectSummary` — id, name, status, created_at, video duration/fps/dims
- `ProjectDetail` — extends summary with pass states
- `JobSummary` — id, project_id, pass_name, status, queued_at, started_at, finished_at, error_message
- `PassStatus` — project_id, pass_name, state, current_job_id, artifact counts
- `ArtifactRef` — id, project_id, pass_name, role, artifact_type, path

Pass 1 specific:

```python
class StableBounds(BaseModel):
    in_time_s: float
    out_time_s: float

class CourtCorner(BaseModel):
    x: float   # pixel coords in median background image
    y: float

class CourtGeometry(BaseModel):
    top_left: CourtCorner
    top_right: CourtCorner
    bottom_left: CourtCorner
    bottom_right: CourtCorner
    net_left: CourtCorner
    net_right: CourtCorner

class BallColorModel(BaseModel):
    hsv_lower: list[float]   # [H, S, V]
    hsv_upper: list[float]

class Pass1RawResult(BaseModel):
    stable_bounds: StableBounds
    court_geometry: CourtGeometry     # system's best guess
    ball_color_model: BallColorModel
    median_background_path: str       # relative to project root
    confidence: dict[str, float]      # per-component confidence scores

class Pass1CorrectionPayload(BaseModel):
    stable_bounds: StableBounds | None = None
    court_geometry: CourtGeometry | None = None
    ball_color_model: BallColorModel | None = None

class Pass1AcceptedOutput(BaseModel):
    stable_bounds: StableBounds
    court_geometry: CourtGeometry
    ball_color_model: BallColorModel
    median_background_artifact_id: str
    calibration_confidence: float
```

**Done when:** `uv run python -c "from pbva_core.enums import PassState; print(PassState.running)"` works.

---

## Step 3 — Persistence Layer (`packages/db`)

### 3.1 SQLAlchemy models (`models.py`)

Implement all five tables from ARCHITECTURE.md §10 using SQLAlchemy 2.x mapped classes:

```python
class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str]           # UUID as str
    name: Mapped[str]
    status: Mapped[str]       # ProjectStatus value
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    root_path: Mapped[str]
    video_path: Mapped[str | None]
    video_duration_s: Mapped[float | None]
    video_fps: Mapped[float | None]
    video_width: Mapped[int | None]
    video_height: Mapped[int | None]

class Pass(Base):
    __tablename__ = "passes"
    id: Mapped[str]
    project_id: Mapped[str]   # FK projects.id
    pass_name: Mapped[str]
    state: Mapped[str]        # PassState value
    current_job_id: Mapped[str | None]
    latest_raw_artifact_id: Mapped[str | None]
    latest_correction_id: Mapped[str | None]
    latest_accepted_artifact_id: Mapped[str | None]
    updated_at: Mapped[datetime]

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str]
    project_id: Mapped[str]
    pass_name: Mapped[str]
    job_type: Mapped[str]
    status: Mapped[str]       # JobStatus value
    attempt: Mapped[int]
    claimed_by: Mapped[str | None]
    queued_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error_message: Mapped[str | None]
    params_json: Mapped[str | None]

class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str]
    project_id: Mapped[str]
    pass_name: Mapped[str | None]
    artifact_role: Mapped[str]   # ArtifactRole value
    artifact_type: Mapped[str]   # "json", "png", "mp4", etc.
    path: Mapped[str]
    sha256: Mapped[str | None]
    created_at: Mapped[datetime]
    job_id: Mapped[str | None]
    summary_json: Mapped[str | None]

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int]          # auto-increment
    project_id: Mapped[str]
    job_id: Mapped[str | None]
    event_type: Mapped[str]
    payload_json: Mapped[str]
    created_at: Mapped[datetime]
```

### 3.2 Migrations

Initialize Alembic in `packages/db/`. Create the initial migration that generates all five tables. Run `uv run alembic upgrade head` to create `data/pbuddy.db`.

### 3.3 Session factory (`session.py`)

```python
def get_engine(db_path: Path) -> Engine
def get_session_factory(engine: Engine) -> sessionmaker
# async-compatible via asyncio.to_thread or sync SQLAlchemy
```

### 3.4 Unit tests

`tests/unit/test_db_models.py` — create a project row, create a pass row, verify retrieval. Use an in-memory SQLite DB (`sqlite:///:memory:`).

**Done when:** `uv run pytest tests/unit/test_db_models.py` passes.

---

## Step 4 — Pass 1 Pipeline (`packages/pipeline/pass1`)

All CV code works on filesystem paths; no HTTP concerns here.

### 4.1 Stable bounds detection (`detect_stable_bounds.py`)

**Input:** video file path, sample rate (default: every 15 frames)

**Algorithm:**
1. Use PyAV to decode every Nth frame (N=15 for test.mp4 → ~2,290 samples).
2. Compute per-frame global motion magnitude using `cv2.calcOpticalFlowFarneback` between consecutive sampled frames, or simpler: mean absolute frame difference.
3. Smooth the motion signal (e.g., rolling median over 5 samples).
4. Find the first contiguous plateau where motion < threshold (stable in-point) and the last such plateau (stable out-point).
5. Convert sample indices to timestamps in seconds.

**Output:** `StableBounds` with `in_time_s` and `out_time_s`.

**Validation with test.mp4:** stable range should be within the first ~30s and last ~30s of the 19-min video.

### 4.2 Background plate generation (`background_plate.py`)

**Input:** video file path, stable bounds, target sample count (default: 300)

**Algorithm:**
1. Evenly space 300 frame timestamps across the stable interval.
2. Decode each frame using PyAV (seek by timestamp).
3. Resize frames to a working resolution (e.g., 960×540) to save memory.
4. Stack into a `(300, H, W, 3)` uint8 array.
5. Compute per-pixel median along axis 0: `np.median(stack, axis=0).astype(np.uint8)`.
6. Save as `passes/pass1/raw/median_background.png` (full working resolution).

**Validation with test.mp4:** the resulting image should show a clear, static court with no players or ball visible.

### 4.3 Court detection (`detect_court.py`)

**Input:** median background image (numpy array or path)

**Algorithm:**
1. Convert to grayscale.
2. Apply Gaussian blur (kernel 5×5).
3. Run Canny edge detection (low=50, high=150).
4. Run probabilistic Hough line transform: `cv2.HoughLinesP(minLineLength=100, maxLineGap=20)`.
5. Cluster nearly-parallel lines into groups; pick the dominant near-horizontal and near-vertical families.
6. From the detected lines, fit a perspective-distorted rectangle:
   - Identify the two sidelines (long near-vertical lines).
   - Identify the baseline (bottom near-horizontal line) and kitchen line or net (upper near-horizontal).
   - Compute four court corners as intersection points.
   - Estimate net midpoints from detected net-area horizontal lines.
7. Return `CourtGeometry` with best-guess corners and net points.
8. Also return a confidence score (0–1) based on how cleanly lines were detected.
9. Write a debug overlay PNG to `passes/pass1/raw/court_overlay.png` showing detected lines and corners for UI display.

**Note on test.mp4:** multiple courts may be visible; pick the primary court as the largest/most complete rectangle closest to image center.

### 4.4 Ball color profiling (`infer_ball_color.py`)

**Input:** video file path, stable bounds, median background

**Algorithm:**
1. Sample 50 frames spread across the stable interval.
2. For each frame, compute absolute difference from the median background.
3. Threshold the difference image to isolate moving foreground blobs.
4. Erode/dilate to remove noise (`cv2.morphologyEx`).
5. Find contours; keep blobs with area in the range expected for a pickleball (diameter ~6cm, appearing as roughly 10–40px diameter at typical camera distances).
6. Extract HSV pixel values from these candidate blobs.
7. Build a histogram of H, S, V values across all candidates.
8. Fit a "generous" bounding box: `hsv_lower` = 10th percentile per channel, `hsv_upper` = 90th percentile per channel.
9. Pickleball colors (optic yellow/green or white): if no confident cluster found, default to a broad yellow-green range.

**Output:** `BallColorModel` and confidence score.

### 4.5 Pass 1 orchestration (`run.py`)

Implement `PipelinePass` protocol (ARCHITECTURE.md §15):

```python
class Pass1(PipelinePass):
    name = "pass1"

    def validate_inputs(self, ctx): ...      # check video file exists
    def load_inputs(self, ctx): ...          # return video path
    def run(self, ctx, progress): ...        # call 4.1→4.4 in sequence with progress reports
    def write_raw_outputs(self, ctx, result): ...   # write files, register artifacts in DB
    def build_accepted_output(self, ctx, raw, corrections): ...  # merge corrections
    def validate_corrections(self, payload): ...    # parse Pass1CorrectionPayload
```

Progress stages emitted (fractions): `detect_stable_bounds` (0.15), `build_background_plate` (0.55), `detect_court` (0.75), `infer_ball_color` (0.90), `write_outputs` (1.0).

### 4.6 Unit tests for Pass 1

`tests/unit/pass1/` — test each sub-module in isolation:
- `test_stable_bounds.py` — use a short synthetic video or mock frame data
- `test_background_plate.py` — verify median correctness on a 5-frame mock
- `test_court_detection.py` — generate a synthetic court image, verify detected corners within tolerance
- `test_ball_color.py` — inject synthetic blobs, verify HSV range

**Done when:** `uv run pytest tests/unit/pass1/` passes and running `Pass1` on `test.mp4` produces `median_background.png` and `court_overlay.png` in the correct directory.

---

## Step 5 — Worker Process (`apps/worker`)

### 5.1 Execution context (`execution_context.py`)

```python
@dataclass
class PassContext:
    project: Project                  # ORM row
    data_root: Path
    settings: Settings
    session_factory: sessionmaker
    job: Job                          # current job row
    progress: ProgressReporter
    # accepted outputs from prior passes (empty for pass1)
    prior_accepted: dict[str, Any]
```

### 5.2 Progress reporter (`progress.py`)

```python
class ProgressReporter:
    def __init__(self, job_id: str, session_factory):
        ...
    def update(self, fraction: float, stage: str, message: str = "") -> None:
        # write Event row to DB; check cancel_requested flag and raise CancelledError
```

### 5.3 Job claiming (`job_claiming.py`)

```python
def claim_next_job(session) -> Job | None:
    """
    Atomically:
    1. SELECT the oldest queued job WHERE no running job exists for same project
    2. UPDATE status='running', started_at=now, claimed_by=worker_pid
    3. Return the Job or None if nothing available
    Use UPDATE ... WHERE status='queued' with row-level locking or a
    serialized transaction to prevent double-claiming.
    """
```

### 5.4 Worker loop (`worker_loop.py`)

```python
def run_worker(settings: Settings) -> None:
    while True:
        with session_factory() as session:
            job = claim_next_job(session)
        if job is None:
            time.sleep(settings.worker_poll_interval_s)
            continue
        try:
            execute_job(job, settings)
        except Exception as e:
            mark_job_failed(job, traceback.format_exc())
```

```python
def execute_job(job: Job, settings: Settings) -> None:
    pass_impl = get_pass(job.pass_name)   # registry lookup
    ctx = build_context(job, settings)
    pass_impl.validate_inputs(ctx)
    raw_result = pass_impl.run(ctx, ctx.progress)
    artifacts = pass_impl.write_raw_outputs(ctx, raw_result)
    with session_factory() as session:
        update_pass_state(session, job, PassState.waiting_for_user, artifacts)
        mark_job_succeeded(session, job)
        write_event(session, job, "pass_waiting_for_user", {})
```

### 5.5 Worker entry point (`main.py`)

```python
if __name__ == "__main__":
    settings = Settings()
    engine = get_engine(settings.db_path)
    run_worker(settings)
```

**Done when:** running `uv run python -m pbva_worker.main` starts polling and logs "Waiting for jobs…" every 2 s.

---

## Step 6 — API Backend (`apps/api`)

### 6.1 App setup (`main.py`, `lifespan.py`)

```python
app = FastAPI(title="PBuddy API")

@asynccontextmanager
async def lifespan(app):
    # create DB engine, run migrations if needed
    # launch worker as subprocess via asyncio.create_subprocess_exec
    # or rely on a separately started worker process
    yield
    # cleanup
```

Bind worker supervisor: on startup, check if worker process is running; if not, spawn it. On shutdown, send SIGTERM.

Include routers: `health`, `projects`, `passes`, `artifacts`, `jobs`.

Mount `GET /api/diagnostics` → report ffmpeg version, OpenCV version, Python version, DB row counts.

### 6.2 Projects routes (`routes/projects.py`)

```
POST /api/projects
  body: { name: str }
  action: create Project row (status=created), create pass1–pass4 Pass rows (state=not_started),
          mkdir project root dirs
  return: ProjectDetail

GET /api/projects
  return: list[ProjectSummary]

GET /api/projects/{project_id}
  return: ProjectDetail

DELETE /api/projects/{project_id}
  action: mark deleted, optionally purge disk files
```

### 6.3 Video ingestion (`routes/projects.py` continued)

```
POST /api/projects/{project_id}/video
  body: multipart file upload
  action:
    1. Write to uploads/original.mp4
    2. Run ffprobe (subprocess) to extract duration, fps, width, height
    3. Update Project row: video_path, video_duration_s, video_fps, video_width, video_height
    4. Update Project status: video_ready → pass1_ready
    5. Register artifact (role=raw, type=mp4)
  return: ProjectDetail

GET /api/projects/{project_id}/video/metadata
  return: { duration_s, fps, width, height }
```

Use `python-multipart` for streaming file upload to avoid loading 19 min video into memory.

### 6.4 Pass routes (`routes/passes.py`)

```
POST /api/projects/{project_id}/passes/pass1/run
  guard: project.status must be pass1_ready
  action: INSERT Job(status=queued), UPDATE Pass(state=queued)
  return: JobSummary

GET /api/projects/{project_id}/passes/{pass_name}
  return: PassStatus

GET /api/projects/{project_id}/passes/{pass_name}/artifacts
  return: list[ArtifactRef]

PUT /api/projects/{project_id}/passes/pass1/corrections
  body: Pass1CorrectionPayload
  guard: pass must be waiting_for_user
  action: validate via Pass1.validate_corrections(), write corrections/latest.json,
          register correction artifact in DB
  return: { ok: true }

POST /api/projects/{project_id}/passes/pass1/accept
  guard: pass must be waiting_for_user
  action:
    1. Load corrections/latest.json (if exists)
    2. Call Pass1.build_accepted_output(raw_result, corrections)
    3. Write accepted/result.json
    4. Register accepted artifact
    5. Update Pass state: accepted
    6. Update Project status: pass1_accepted
    7. Write Event: pass_accepted
  return: { ok: true, data: Pass1AcceptedOutput }
```

### 6.5 Artifact serving (`routes/artifacts.py`)

```
GET /api/artifacts/{artifact_id}
  action: look up path in DB, validate it's within data_root,
          stream file (PNG → image/png, JSON → application/json, MP4 → video/mp4 with range support)

GET /api/projects/{project_id}/frames/{frame_index}
  action: decode and return a single JPEG frame from the original video using PyAV
```

### 6.6 WebSocket (`websocket_manager.py`)

```python
class ConnectionManager:
    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, project_id: str, ws: WebSocket)
    async def disconnect(self, project_id: str, ws: WebSocket)
    async def broadcast(self, project_id: str, message: dict)
```

Poll the `events` table for new rows (watermark per project_id) every 1 s inside an async background task, broadcast to connected clients. This avoids cross-process communication complexity.

```
WS /ws/projects/{project_id}
```

### 6.7 Integration tests (`tests/integration/`)

`test_project_lifecycle.py` using `httpx.AsyncClient`:

1. `POST /api/projects` → get project_id
2. `POST /api/projects/{id}/video` upload a tiny synthetic MP4 (< 5 s, generated with ffmpeg in test fixture)
3. `GET /api/projects/{id}` → status is `pass1_ready`
4. `POST /api/projects/{id}/passes/pass1/run` → job queued
5. Poll `GET /api/projects/{id}/passes/pass1` until state is `waiting_for_user` (or failed)
6. `PUT …/corrections` → submit identity correction payload
7. `POST …/accept` → accepted
8. `GET /api/projects/{id}` → status is `pass1_accepted`
9. Restart: open a new API client, `GET /api/projects/{id}` → state still `pass1_accepted`

**Done when:** `uv run pytest tests/integration/` passes end-to-end.

---

## Step 7 — Frontend (`apps/frontend`)

### 7.1 Bootstrap

```bash
cd apps/frontend
npm create vite@latest . -- --template react-ts
npm install @tanstack/react-query zustand react-router-dom
```

Configure Vite proxy so `fetch('/api/...')` hits `localhost:8000` during dev.

### 7.2 API client (`src/api/client.ts`)

Thin typed wrapper around `fetch`:

```typescript
export const api = {
  createProject: (name: string) => post<ProjectDetail>('/api/projects', { name }),
  uploadVideo: (projectId: string, file: File) => postFormData(`/api/projects/${projectId}/video`, file),
  getProject: (id: string) => get<ProjectDetail>(`/api/projects/${id}`),
  listProjects: () => get<ProjectSummary[]>('/api/projects'),
  runPass1: (id: string) => post<JobSummary>(`/api/projects/${id}/passes/pass1/run`),
  getPass: (id: string, passName: string) => get<PassStatus>(`/api/projects/${id}/passes/${passName}`),
  getArtifacts: (id: string, passName: string) => get<ArtifactRef[]>(`/api/projects/${id}/passes/${passName}/artifacts`),
  submitCorrections: (id: string, corrections: Pass1CorrectionPayload) =>
    put(`/api/projects/${id}/passes/pass1/corrections`, corrections),
  acceptPass1: (id: string) => post(`/api/projects/${id}/passes/pass1/accept`),
}
```

### 7.3 WebSocket client (`src/api/ws.ts`)

```typescript
export function useProjectWebSocket(projectId: string, onMessage: (msg: WsEvent) => void)
```

Reconnect automatically on disconnect with exponential backoff. Invalidate relevant TanStack Query caches on `pass_waiting_for_user` and `pass_accepted` events.

### 7.4 Pages and routing (`src/app/routes.tsx`)

```
/                        → ProjectListPage  (list existing projects, create new)
/projects/:id            → ProjectHome      (project overview, pass status cards)
/projects/:id/pass1      → Pass1Page        (review UI)
```

### 7.5 `ProjectListPage`

- List cards for each project (name, status, created date).
- "New Project" button → dialog: enter name, upload video file → calls `createProject` then `uploadVideo`.
- Navigate to `/projects/:id` after upload completes.

### 7.6 `ProjectHome`

- Show project metadata (video duration, resolution).
- Pass status cards for Pass 1–4.
- Pass 1 card: if `pass1_ready`, show "Run Pass 1" button.
- If `running` or `queued`, show progress bar (fed from WebSocket `job_progress` events).
- If `waiting_for_user`, show "Review" button → navigate to `/projects/:id/pass1`.

### 7.7 `Pass1Page` — the core review UI

Layout: two-column. Left = controls, Right = image canvas.

**Median background viewer:**
- Load `median_background.png` from `GET /api/artifacts/{artifact_id}`.
- Display at full width of canvas.

**Court geometry overlay (Canvas/SVG):**
- Draw the detected court polygon over the background image.
- Six draggable handles: four court corners + two net endpoints.
- Handles are initially positioned from `Pass1RawResult.court_geometry`.
- Dragging updates local Zustand `editorStore.courtGeometry`.

**Timeline range selector:**
- Two sliders (in-time, out-time) over the video duration.
- Display timestamps as MM:SS.
- Initialized from `Pass1RawResult.stable_bounds`.

**Ball color profile widget:**
- Display two color swatches (HSV lower/upper bounds rendered as sRGB approximation).
- "Click ball in frame" mode: show a frame at `stable_bounds.in_time_s + 5s`, let user click a pixel, sample a small neighborhood, recompute generous bounds.

**Action bar:**
- "Save corrections" → PUT corrections endpoint with current `editorStore` state.
- "Accept" → POST accept endpoint → navigate back to ProjectHome.
- Unsaved changes indicator: show warning if `editorStore` differs from last saved.

### 7.8 Progress display

`ProgressPanel` component: poll `GET /api/projects/:id/passes/pass1` every 3 s while status is `running`, or update via WebSocket `job_progress` events. Show stage name and fraction as a progress bar.

**Done when:** the complete UI flow runs in the browser against a live API and worker.

---

## Step 8 — Integration & Validation with `test.mp4`

### 8.1 Smoke test (manual)

1. Start API and worker: `bash scripts/dev_api.sh` and `bash scripts/dev_worker.sh` in separate terminals.
2. Start frontend: `bash scripts/dev_frontend.sh`.
3. Open browser at `http://localhost:5173`.
4. Create a new project named "Morning Games 3-10-26 Game 2".
5. Upload `test.mp4` (symlink to ~19-min video). Verify status becomes `pass1_ready`.
6. Click "Run Pass 1". Watch the progress bar advance through stages. Expected runtime: 3–10 min on Apple Silicon (median of 300 frames is the bottleneck).
7. When complete, open the Pass 1 review page.
8. **Verify the median background image** shows a static court with no players or ball.
9. **Verify court corners** are reasonably placed on the court lines. Minor misalignment is expected; drag to correct.
10. **Verify stable bounds** exclude initial camera panning and final camera motion.
11. Submit corrections, click Accept.
12. Verify project status becomes `pass1_accepted` and is persisted through browser reload.

### 8.2 Pass 1 acceptance criteria for `test.mp4`

| Check | Pass criterion |
|-------|---------------|
| Median background | Court clearly visible, no player or ball artifacts |
| Stable bounds | In-point ≤ 30 s from start; out-point ≥ 30 s before end |
| Court detection confidence | ≥ 0.5 (system detects at least 4 plausible lines) |
| Ball color profiling | HSV range is non-empty and covers yellow-green spectrum |
| End-to-end runtime | Pass 1 completes in < 15 min on Apple M-series |
| Persistence | After browser reload, `pass1_accepted` status is intact |

### 8.3 Automated regression test with `test.mp4`

`tests/integration/test_pass1_full.py`:

```python
@pytest.mark.slow   # skip in CI; run manually with: uv run pytest -m slow
def test_pass1_smoke(tmp_path):
    settings = Settings(data_root=tmp_path)
    # Copy or symlink test.mp4 into uploads
    # Run Pass1 directly (bypassing HTTP/worker for speed)
    ctx = build_test_context(settings, video_path="test.mp4")
    pass1 = Pass1()
    result = pass1.run(ctx, NullProgressReporter())
    assert result.stable_bounds.in_time_s < 30
    assert result.stable_bounds.out_time_s > result.stable_bounds.in_time_s + 600
    assert result.court_geometry is not None
    assert result.confidence["court"] > 0.5
    bg = cv2.imread(str(ctx.paths.pass_raw_dir / "median_background.png"))
    assert bg is not None
    assert bg.shape == (540, 960, 3)   # working resolution
```

---

## Milestone Done Criteria

The milestone is complete when all of the following are true:

- [ ] `uv sync` installs all dependencies cleanly
- [ ] `uv run pytest tests/unit/` passes (no integration tests required)
- [ ] Worker starts, polls, and executes Pass 1 without crashing on `test.mp4`
- [ ] Median background image visually shows a clean static court
- [ ] Court corner overlay is visible and draggable in the browser
- [ ] Corrections can be submitted and the `corrections/latest.json` file is written to disk
- [ ] Accepting Pass 1 writes `accepted/result.json` and updates project status to `pass1_accepted`
- [ ] Reloading the browser or restarting the API server shows the correct project state
- [ ] `tests/integration/test_project_lifecycle.py` passes with a synthetic video

Once this milestone is solid, Pass 2 can be built with confidence that the architectural skeleton is correct.
