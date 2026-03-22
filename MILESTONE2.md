# Milestone 2 — Pass 2: Moving Object Detection

## Overview

**Note:** This milestone redefines Pass 2 relative to `PIPELINE.md`. The original plan called Pass 2 "Temporal Segmentation", but detecting moving objects is a prerequisite for that work, and the output of this pass is directly useful for temporal segmentation in a later milestone. The temporal segmentation goal is deferred to Milestone 3.

**Goal of this pass:** For every frame within the stable video bounds accepted in Pass 1, detect foreground blobs by differencing against the median background plate. No identification or frame-to-frame linking is performed — this pass produces a raw catalogue of "something moved here" annotations per frame.

**Review UI goal:** A video player that renders the normalized video with detection overlays and an optional court outline, supporting smooth playback at multiple speeds in both directions.

---

## 1. Algorithm: Background Subtraction

**Inputs (from Pass 1 accepted output):**
- `median_background.png` — stored at native video resolution up to 1920×1080 (see Pass 1 change below)
- `stable_bounds` — in/out times defining which frames to process
- `court_geometry` — four corner points in background image pixel coordinates
- `bg_width`, `bg_height` — actual pixel dimensions of the background image

### Why full video resolution

Working at the video's native resolution matters because the ball is small:

| Resolution | mm/px (typical) | Ball diameter | Ball at 40 mph, 1/120s shutter |
|------------|----------------|--------------|-------------------------------|
| 960×540    | ~9 mm/px       | ~8 px        | 8 px × 16 px (marginal)       |
| 1920×1080  | ~4.5 mm/px     | ~16 px       | 16 px × 33 px (reliable)      |

At 960×540 the ball blob is ~8 pixels in diameter — barely above the minimum for `cv2.fitEllipse` (which requires ≥5 contour points), and a noisy fit. At full 1080p it is ~16 pixels with a much more reliable contour. Area and circularity filters that Pass 3 will depend on are meaningless at 8-pixel scale.

**Pass 1 change (implemented):** `scan.py` now stores the median background at the video's native resolution, capped at 1920×1080 (aspect ratio preserved). Videos shot at 4K are downscaled to fit within this cap, keeping peak memory during median computation at ~1.86 GB. The background plate resolution is recorded as `bg_width`/`bg_height` in `Pass1RawResult` and `Pass1AcceptedOutput`, eliminating all hardcoded 960×540 references from both the backend and frontend. `background_plate.py` (previously unused) was removed.

The `bg_width`/`bg_height` fields in `detections.json` record the resolution at which detections were computed (same as the background image).

**Processing per frame:**

1. **Decode** the frame from `normalized.mp4` at native resolution. If it differs from `bg_width`×`bg_height`, resize to match before differencing (handles videos where the stream resolution doesn't exactly equal the background plate resolution).
2. **Absolute difference** vs. the median background → 3-channel diff image.
4. **Grayscale collapse** → single-channel diff.
5. **Threshold** at a fixed value (default 30, tunable via config). Pixels above threshold become foreground.
6. **Morphological open** (small kernel, 1 iteration) to remove isolated noise pixels.
7. **Morphological close** (slightly larger kernel, 2 iterations) to fill gaps within blobs.
8. **Connected components** — label distinct foreground regions.
9. **Filter by area** — discard regions smaller than `min_area` (default: 300 px² at 1080p) or larger than `max_area` (default: 160,000 px²). This removes noise specks and whole-frame illumination shifts.
10. **Fit ellipse** to each surviving region's contour (see §2).
11. **Record detection** with ellipse parameters, bounding box, and area.

Processing only runs within the stable bounds (skipping the trimmed head/tail). Frames are processed sequentially; there is no temporal filtering at this stage.

---

## 2. Detection Representation: Fitted Ellipse

### Why not a plain bounding box

A bounding box stores 4 numbers (x, y, w, h) and loses shape information. For Pass 3, which needs to distinguish balls from players from background clutter, shape is critical:

- **Ball at rest or slow**: near-circular, small (~16 px diameter). High circularity (b/a close to 1).
- **Ball in fast motion (40 mph, 1/120s shutter)**: motion-blurred into a ~16×33 px streak. The blob is the sphere convolved with its motion vector, producing a stadium shape (rectangle with rounded caps) — well approximated by an elongated ellipse. The major-axis angle encodes the direction of travel; the b/a ratio encodes how much blur is present. This information is useful for velocity initialization in Pass 3.
- **Player**: large area, aspect ratio reflecting stance (upright player b/a ≈ 0.4; crouching b/a ≈ 0.7).
- **Noise/clutter**: small area, erratic shape.

### Why not a fixed-N polygon

A convex hull resampled to N vertices gives a better approximation of a player silhouette than an ellipse. However:
- Pass 3 tracks players using **appearance features** (color histogram, HOG) within the bounding box, not the silhouette contour
- The foreground mask from thresholded background subtraction is inherently noisy; morphological cleanup makes the blob roughly convex anyway, limiting the fidelity gain
- A polygon stores 2N numbers vs 5 for an ellipse, is more complex to render, and is harder to compare across frames

The ellipse's `b/a` and `area` are sufficient discriminators between balls, players, and clutter at this stage. A polygon representation can be revisited if Pass 3 turns out to need finer shape features.

### Detection schema

Each detection is a fixed-width record:

| Field | Type | Description |
|-------|------|-------------|
| `frame_index` | int | 0-based frame number |
| `frame_time_s` | float | Timestamp in seconds |
| `cx`, `cy` | float | Ellipse centroid in 960×540 image coords |
| `a` | float | Semi-major axis length (px) |
| `b` | float | Semi-minor axis length (px); circularity = b/a |
| `angle` | float | Orientation of major axis (degrees, OpenCV convention) |
| `area` | int | Connected component area (px²) |
| `bbox_x`, `bbox_y`, `bbox_w`, `bbox_h` | int | Axis-aligned bounding box |

Circularity `b/a` is not stored but computed on demand. A ball candidate has `b/a > 0.75` and `area < 2000`. A player has `area > 5000`.

**Ellipse fitting caveat:** `cv2.fitEllipse` requires ≥ 5 contour points. For very small blobs, fall back to a circle: `a = b = sqrt(area / π)`, `angle = 0`.

---

## 3. Artifact Storage

### On disk

```
passes/pass2/
  raw/
    result.json          # summary: frame count, detection count, thresholds, fps
    detections.json      # full per-frame detection data (see format below)
  corrections/           # unused in this milestone (no user corrections)
  accepted/
    result.json          # copy of raw/result.json (no merging needed this milestone)
    detections.json      # symlink or copy of raw/detections.json
```

### `detections.json` format

```json
{
  "fps": 29.97,
  "bg_width": 1920,
  "bg_height": 1080,
  "frame_count": 36142,
  "detection_count": 287341,
  "frames": {
    "1200": [
      {"cx": 450.2, "cy": 312.1, "a": 14.5, "b": 13.8, "angle": 12.3,
       "area": 621, "bbox_x": 436, "bbox_y": 298, "bbox_w": 29, "bbox_h": 28},
      ...
    ],
    "1201": [ ... ],
    ...
  }
}
```

Keys are string frame indices. Frames with zero detections are omitted.

**Why JSON and not Parquet here:** The frontend needs to load this data without special binary libraries. JSON is straightforward. File size estimate for a 20-minute video at 30 fps with ~8 detections/frame: ~15–25 MB uncompressed, ~3–5 MB gzipped. Acceptable for a local app. If performance becomes an issue, switch to a binary format served via a more capable API endpoint.

**Why not store detections in SQLite:** At ~8M rows for a 20-minute video this would stress SQLite and add no benefit over a flat file for this read-once, iterate-forward workload. SQLite stores the *artifact reference* only; the file holds the data.

### DB artifact registration

Two artifacts are registered:
- `raw` / `json` → `raw/result.json`
- `raw` / `json` → `raw/detections.json` (artifact_role includes `name: "detections"` in summary_json for lookup)

---

## 4. API Additions

No new endpoints are needed beyond what already exists. The standard pass control endpoints handle run/accept. The detections artifact is fetched via the existing `GET /api/artifacts/{artifact_id}` endpoint.

The Pass 2 page needs to discover the detections artifact ID. Pattern: call `GET /api/projects/{id}/passes/pass2/artifacts` and find the artifact with `summary_json.name == "detections"`.

---

## 5. Frontend: Video Player Design

### 5.1 Video element and canvas overlay

Use a **stacked layout**: the HTML5 `<video>` element underneath, a `<canvas>` element on top with `position: absolute`, both sized to the same display dimensions. On window resize, the canvas is re-sized to match the video's rendered dimensions.

The video source is `normalized.mp4` (served via artifact URL). This is the full-resolution normalized video, not the 960×540 background plate.

**Coordinate scaling:** Detections are stored at 960×540. At render time, scale them to the displayed video size:

```ts
const scaleX = videoElement.clientWidth / 960
const scaleY = videoElement.clientHeight / 540
```

### 5.2 Forward playback

Use `videoElement.playbackRate` directly:

```
Speed: 0.25×, 0.5×, 1.0×, 3.0×
```

HTML5 supports positive `playbackRate` values reliably across browsers. Binding the speed buttons to `videoElement.playbackRate = value` is sufficient.

### 5.3 Reverse playback

HTML5 does **not** reliably support negative `playbackRate` (it is non-standard and Safari rejects it). Implement reverse as manual `currentTime` decrement:

1. Pause the video element.
2. Start a `requestAnimationFrame` loop.
3. Each frame, decrement `video.currentTime` by `(1 / fps) * speedFactor`.
4. After setting `currentTime`, wait for the `seeked` event before rendering the overlay, then request the next frame.

This produces smooth-enough backward scrubbing at low speeds (0.25×, 0.5×). At 3× backward it may drop frames due to seek latency, but that is acceptable — 3× reverse is primarily for quick navigation, not smooth playback.

**State machine for the player:**

```
stopped → playing-forward
        → playing-backward
playing-forward → stopped (pause button, end of video)
playing-backward → stopped (pause button, start of video)
```

Speed and direction are independent. Switching direction always passes through `stopped`.

### 5.4 Overlay rendering

On each rendered video frame, look up detections for the current frame index:

```ts
const frameIndex = Math.round(video.currentTime * fps)
const dets = detectionsIndex[frameIndex] ?? []
```

For each detection, draw on the canvas:
- **Ellipse**: use `ctx.ellipse(cx * scaleX, cy * scaleY, a * scaleX, b * scaleY, angleRad, 0, 2 * Math.PI)` with a semi-transparent stroke.
- **No fill** (or very low opacity fill) to keep the video visible.

Sync the overlay drawing to the `timeupdate` event for forward playback, and to the `seeked` event for backward frame-stepping.

### 5.5 Detections data loading

At page load, the Pass 2 review page:
1. Fetches the `detections.json` artifact in a single request.
2. Stores it in component state as a `Record<number, Detection[]>` (keyed by frame index, with numeric keys parsed from the JSON string keys).
3. Displays a loading spinner until the fetch completes.

This is a one-time cost. Subsequent frame lookups are O(1) dictionary access.

### 5.6 Court outline overlay (optional)

A toggle button shows/hides the court overlay. When enabled, draw the four court corners and connecting lines (baseline, sidelines, net line, NVZ line at 2.13 m from net) using the accepted Pass 1 `court_geometry` (which is already in 960×540 image coordinates, so apply the same scaleX/scaleY).

The existing `CourtOverlay` component was built for the Pass 1 static image viewer. For Pass 2, a simplified read-only version is sufficient (no dragging).

### 5.7 UI layout

```
┌─────────────────────────────────────────────────────┐
│  Pass 2 Review                          [Accept →]  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │                                               │  │
│  │           video + canvas overlay             │  │
│  │                                               │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  [◀◀] [◀] [▶/⏸] [▶▶]   Speed: [0.25] [0.5] [1] [3]│
│  Time: 0m 12s / 18m 44s                            │
│  Frame: 362 / 33720                                │
│  Detections this frame: 6                          │
│  ☐ Show court outline                              │
└─────────────────────────────────────────────────────┘
```

`[◀◀]` = 3× backward, `[◀]` = 1× backward, `[▶▶]` = 3× forward. `[▶/⏸]` = play/pause in current direction at current speed.

### 5.8 Accept action

No corrections are submitted. Clicking **Accept** calls `POST /api/projects/{id}/passes/pass2/accept` with an empty correction payload. The backend copies the raw artifacts to `accepted/` and transitions the pass state to `accepted`. The project state advances to `pass2_accepted`, enabling Pass 3.

---

## 6. Implementation Tasks

### Backend pipeline (`packages/pipeline/`)

- [ ] `pass2/run.py` — Pass 2 orchestrator implementing `PipelinePass` protocol
- [ ] `pass2/detect_blobs.py` — frame iteration, background subtraction, blob detection, ellipse fitting, detection serialization
- [ ] `pass2/schemas.py` — `Pass2RawResult`, `Pass2CorrectionPayload` (empty for now), `Pass2AcceptedOutput` Pydantic models

### Backend API (`apps/api/`)

- [ ] Register `Pass2` in the pass registry so `POST /passes/pass2/run` and `POST /passes/pass2/accept` route to it
- [ ] `schemas/pass2.py` — API-facing Pydantic schemas matching pipeline schemas

### Frontend (`apps/frontend/`)

- [ ] `pages/Pass2Page.tsx` — top-level page: artifact loading, detections fetch, accept action
- [ ] `components/VideoPlayer.tsx` — reusable player: video element + canvas, playback controls, direction, speed
- [ ] `components/DetectionOverlay.tsx` — canvas rendering of ellipses, court outline toggle
- [ ] Wire `Pass2Page` into `App.tsx` routes

### Tests

- [ ] `tests/unit/pass2/test_detect_blobs.py` — unit test: synthetic frame with a known blob, verify detected ellipse is within tolerance
- [ ] `tests/integration/test_pass2_lifecycle.py` — integration test: run pass 2 on test project, verify artifact files exist and result JSON is valid

---

## 7. Open Questions / Deferred

- **Threshold tuning**: The default threshold of 30 (out of 255) is a starting guess. After running on real footage, this may need to be a per-project parameter. Deferred: could add a "re-run with different threshold" option in a future milestone.
- **Shadow detections**: The ball's shadow on the court will appear as a separate blob. This is fine for now — Pass 3 will filter by size and color. No special handling needed here.
- **Court-side masking**: Blobs outside the court region could be masked to reduce noise in cluttered environments. Deferred to Pass 3.
- **Lighting changes**: Gradual illumination shifts (cloud cover) can cause large-area foreground detections. The `max_area` filter provides a crude safeguard. A more robust approach (adaptive background model) is deferred.
- **Codec seek precision**: For backward frame-stepping, seeking to exact frames in H.264 video may land on the nearest keyframe. Acceptable for review purposes; frame-accurate decoding is not required until Pass 3 tracking.
