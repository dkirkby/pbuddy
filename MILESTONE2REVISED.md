# Milestone 2 Revised — Ball Annotation Tool

## Why We're Changing Approach

Background subtraction produced too many false positives (moving people at adjacent courts,
shadows, net vibration) and missed the ball when its color matched the court surface. Rather
than tuning an unreliable heuristic detector, we shift to a **human-in-the-loop** workflow:
the user clicks on the ball in individual frames to build a ground-truth annotation dataset.
This dataset will drive a learned detector in Pass 3 (e.g. TrackNetV2 or a lightweight CNN
trained on these crops) and also serves directly as sparse ball-position data for trajectory
reconstruction.

---

## New Pass 2 Goal

**Output**: A sparse set of manually annotated ball positions — `(frame_index, x, y)` in
background-plate pixel space — stored as `ball_annotations.json`. One click per frame the
user can find the ball; no click required for frames where the ball is hidden or off-screen.

---

## UX Design

### Pass 2 "Run" (trivial — replaces blob detection)

Pass 2 run still exists to fit the existing pipeline pattern, but it now just:
1. Reads video metadata (fps already known).
2. Confirms the median background plate from Pass 1 exists.
3. Writes a minimal `result.json` (fps, bg_width, bg_height).

This completes in under a second. No blob detection.

### Pass 2 "Review" — Annotation UI (`Pass2Page.tsx`)

The review page shows the video player (existing controls unchanged) plus an annotation
layer. Layout:

```
[video with canvas overlay]

[⏮] [◀◀] [◀|] [▶/⏸] [|▶] [▶▶] [?]   Court □    Balls marked: 42

[Save Annotations]   [Accept →]
```

**Interaction**:

- **Click on the video** → records ball center for the current frame in bg-plate pixel
  coordinates. Replaces any existing annotation for that frame.
- **Right-click on the video** (or a Delete key shortcut) → removes the annotation for the
  current frame.
- **"+" overlay** → a small orange cross is drawn at the annotated position whenever the
  displayed frame has an annotation.  All annotations within ±5 frames are also shown faded
  (50 % opacity) to help judge ball continuity while stepping.
- **Ball count** → "Balls marked: N" displayed next to the playback controls, updated live.
- **Save Annotations** → `PUT /api/projects/{id}/passes/pass2/corrections` with the full
  annotation dict. Enabled whenever there are unsaved changes.
- **Accept →** → saves (if dirty) then `POST /api/projects/{id}/passes/pass2/accept`.

### Coordinate system

All stored coordinates are in the **background-plate pixel space** (bg_width × bg_height),
matching the coordinate system used in the existing detection overlays. Converting from a
click on the displayed `<video>` element:

```
bg_x = click_x / video.clientWidth  * bg_width
bg_y = click_y / video.clientHeight * bg_height
```

---

## Data Formats

### `raw/result.json` (Pass2RawResult)

```json
{
  "fps": 59.940060,
  "bg_width": 1920,
  "bg_height": 1080
}
```

### `corrections/annotations.json` (Pass2CorrectionPayload)

```json
{
  "annotations": {
    "1234": { "x": 847.3, "y": 612.1 },
    "1235": { "x": 851.0, "y": 608.4 },
    "1301": { "x": 920.5, "y": 580.0 }
  }
}
```

Keys are frame indices (as strings, matching the existing `detections.json` convention).
Values are ball-centre pixel coordinates in bg-plate space.

### `accepted/annotations.json` (Pass2AcceptedOutput)

Same schema as corrections. Accept copies the file and records the artifact.

---

## Backend Changes

### `packages/core/src/pbva_core/types.py`

Replace the blob-detection fields in `Pass2RawResult` with just the video metadata:

```python
class Pass2RawResult(BaseModel):
    fps: float
    bg_width: int
    bg_height: int

class Pass2CorrectionPayload(BaseModel):
    annotations: dict[str, dict]   # frame_index_str -> {x, y}

class Pass2AcceptedOutput(BaseModel):
    fps: float
    bg_width: int
    bg_height: int
    annotation_count: int
```

### `packages/pipeline/src/pbva_pipeline/pass2/run.py`

Replace blob detection with a fast metadata read:

```python
def run(self, ctx, progress=None):
    p1 = Pass1AcceptedOutput.model_validate(ctx.prior_accepted)
    bg_path = ctx.paths.project_root / "passes/pass1/raw/median_background.png"
    if not bg_path.exists():
        raise FileNotFoundError(...)
    result = Pass2RawResult(
        fps=ctx.video_fps,
        bg_width=p1.bg_width,
        bg_height=p1.bg_height,
    )
    (raw_dir / "result.json").write_text(result.model_dump_json(indent=2))
    return result
```

Remove `detect_blobs.py` (or keep it dormant for reference).

### `apps/api/src/pbva_api/routes/passes.py`

- Add `GET /passes/pass2/corrections` → returns saved `annotations.json` (or `{}` if none).
- Add `PUT /passes/pass2/corrections` → validates and saves `annotations.json`.
- Update `accept_pass2` → copies `corrections/annotations.json` to `accepted/annotations.json`;
  builds `Pass2AcceptedOutput` with `annotation_count`.
- Remove pause/resume/cancel logic added for blob detection (no longer needed).

---

## Frontend Changes

### `apps/frontend/src/pages/Pass2Page.tsx`

Full rewrite as annotation UI:

- Load `raw/result.json` for fps/dimensions.
- Load saved corrections (`GET /passes/pass2/corrections`) on mount to pre-populate
  the annotation dict.
- Track `annotations: Record<number, {x: number, y: number}>` in local state.
- Pass a `onVideoClick` handler into `VideoPlayer` (new optional prop).
- On click: convert to bg-plate coords, update annotations, mark dirty.
- Pass `annotations` to `VideoPlayer` for the "+" overlay.
- Show "Balls marked: N" in the control bar.
- "Save Annotations" button calls `PUT /passes/pass2/corrections`.
- "Accept →" button saves then calls `POST /passes/pass2/accept`.

### `apps/frontend/src/components/VideoPlayer.tsx`

Minimal changes — add two optional props:

```typescript
interface Props {
  ...existing...
  annotations?: Record<number, { x: number; y: number }>   // ball marks
  onVideoClick?: (bgX: number, bgY: number) => void         // annotation callback
}
```

Draw "+" markers in `drawOverlay`:
- Current frame annotation: full-opacity orange cross, arm length ~10 px on canvas.
- Annotations within ±5 frames: 30 % opacity, same cross.

Wire `onClick` on the `<video>` element (or the wrapping div) to `onVideoClick` after
converting from client coords to bg-plate coords.

### `apps/frontend/src/api/client.ts`

```typescript
getPass2Corrections: (projectId) =>
  get(`.../passes/pass2/corrections`),

savePass2Annotations: (projectId, annotations) =>
  put(`.../passes/pass2/corrections`, { annotations }),
```

### `apps/frontend/src/types/api.ts`

```typescript
export interface BallAnnotation { x: number; y: number }
export interface Pass2Corrections {
  annotations: Record<string, BallAnnotation>
}
export interface Pass2RawResult {
  fps: number; bg_width: number; bg_height: number
}
```

---

## What Stays the Same

- Video playback controls (all six actions + help panel) — unchanged.
- Court overlay — unchanged.
- Pass 2 run/accept state machine in the worker — unchanged.
- Artifact registration and `write_raw_outputs` pattern — unchanged.
- `ProjectHome.tsx` Pass 2 card — minor label update ("Annotation" instead of "Detection");
  remove the pause/resume/continue buttons (no long-running job anymore).

---

## Out of Scope for This Milestone

- Automatic ball detection / TrackNet (Pass 3).
- Interpolating ball positions between annotated frames.
- Exporting image crops for model training.
- Validating annotation quality (e.g. flagging suspiciously far jumps between frames).
