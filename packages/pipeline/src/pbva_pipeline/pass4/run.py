"""Pass 4 — Ball Detection: per-frame motion+color+silhouette mask over the stable video range."""

from __future__ import annotations

import json
import time

import cv2
import numpy as np

from pbva_core.types import Pass1AcceptedOutput
from pbva_pipeline.base import PassContext

# Must match Pass 3 histogram bin counts.
_H_BINS, _S_BINS, _V_BINS = 48, 48, 8

# Per-frame median background — must match Pass 3 _write_bg_colors parameters.
_GAP    = 15
_SPAN   = 5
_N_SIDE = 15
_MEDIAN_OFFSETS = sorted(
    [-_GAP - k * _SPAN for k in range(_N_SIDE)] +
    [ _GAP + k * _SPAN for k in range(_N_SIDE)]
)
# Gap-zone offsets: multiples of _SPAN strictly inside the gap (±5, ±10, 0).
_GAP_OFFSETS = sorted(k * _SPAN for k in range(-(_GAP // _SPAN - 1), _GAP // _SPAN))


def _fetch_frame(
    cap: cv2.VideoCapture, fi: int, cache: dict[int, np.ndarray], total_frames: int
) -> np.ndarray | None:
    """Return decoded frame fi using/populating cache. Returns None if out of bounds or read fails."""
    if fi in cache:
        return cache[fi]
    if not (0 <= fi < total_frames):
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
    ok, frame = cap.read()
    if ok:
        cache[fi] = frame
    return frame if ok else None


def _compute_median(cache: dict[int, np.ndarray], center: int, total_frames: int) -> np.ndarray | None:
    frames = [cache[center + o] for o in _MEDIAN_OFFSETS
              if 0 <= center + o < total_frames and (center + o) in cache]
    if not frames:
        return None
    return np.median(np.stack(frames, axis=0), axis=0).astype(np.uint8)


def detect_motion(frame, bg_blur, close_kernel, blur=3, threshold=25):
    frame_blur = cv2.medianBlur(frame, blur)
    diff = cv2.absdiff(frame_blur, bg_blur)
    motion = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, moving = cv2.threshold(motion, threshold, 255, cv2.THRESH_BINARY)
    moving = cv2.medianBlur(moving, 3)
    solid  = cv2.morphologyEx(moving, cv2.MORPH_CLOSE, close_kernel)
    return solid


class Pass4:
    name = "pass4"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.video_path.exists():
            raise FileNotFoundError(f"Video not found: {ctx.video_path}")
        pass1_accepted = ctx.paths.project_root / "passes" / "pass1" / "accepted"
        if not (pass1_accepted / "result.json").exists():
            raise FileNotFoundError("Pass 1 accepted result.json not found")
        if not (pass1_accepted / "tent_mask.png").exists():
            raise FileNotFoundError("Pass 1 tent_mask.png not found")
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        if not rally_path.exists():
            raise FileNotFoundError("Pass 2 accepted rally.json not found — record rallies in Pass 2 first")
        mask_path = ctx.paths.project_root / "passes" / "pass3" / "raw" / "Pratio_mask.npz"
        if not mask_path.exists():
            raise FileNotFoundError("Pass 3 Pratio_mask.npz not found — run Pass 3 first")

    def run(self, ctx: PassContext, progress=None):
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.02, "setup", "Loading pass 1 accepted output…")
        pass1_dir = ctx.paths.project_root / "passes" / "pass1"
        p1 = Pass1AcceptedOutput.model_validate_json(
            (pass1_dir / "accepted" / "result.json").read_text()
        )
        in_time_s  = p1.stable_bounds.in_time_s
        out_time_s = p1.stable_bounds.out_time_s

        progress.update(0.04, "setup", "Loading tent mask…")
        tent_mask = cv2.imread(str(pass1_dir / "accepted" / "tent_mask.png"), cv2.IMREAD_GRAYSCALE)
        bg_h, bg_w = tent_mask.shape[:2]

        progress.update(0.05, "setup", "Loading ball radius, annotations, and rally bounds from pass 2…")
        ann_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "annotations.json"
        _ann_data = json.loads(ann_path.read_text()).get("annotations", {}) if ann_path.exists() else {}
        _radii = [v.get("radius", 0) for v in _ann_data.values() if v.get("radius", 0) > 0]
        max_ball_radius = round(max(_radii)) if _radii else 16
        min_blob_radius = (round(min(_radii)) if _radii else 4) / 4

        # Rally frame numbers are browser-side (OpenCV frame_idx + 1); convert to OpenCV indices.
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        rally_intervals: list[tuple[int, int]] = sorted(
            (r["start_frame"] - 1, r["stop_frame"] - 1)
            for r in json.loads(rally_path.read_text()).get("rally", [])
        )

        ann_by_frame: dict[int, dict] = {}
        if ann_path.exists():
            raw_ann = json.loads(ann_path.read_text()).get("annotations", {})
            ann_by_frame = {int(k): v for k, v in raw_ann.items()}

        progress.update(0.06, "setup", "Loading LR mask from pass 3…")
        mask_path = ctx.paths.project_root / "passes" / "pass3" / "raw" / "Pratio_mask.npz"
        lr_blurred: np.ndarray = np.load(str(mask_path))["mask"]  # (H_BINS, S_BINS, V_BINS), bool

        progress.update(0.08, "setup", "Opening video…")
        cap = cv2.VideoCapture(str(ctx.video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {ctx.video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or ctx.video_fps
        in_frame  = max(0, int(in_time_s  * fps))
        out_frame = int(out_time_s * fps)
        video_total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        pause_file  = raw_dir / ".pause"
        patches_dir = raw_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)

        close_kernel    = np.ones((5, 5), np.uint8)
        max_blob_radius = max_ball_radius + close_kernel.shape[0]
        half = 32   # patch half-size → 64×64 output

        stable_frame_count = max(0, out_frame - in_frame + 1)
        total_rally_frames = sum(e - s + 1 for s, e in rally_intervals)
        in_rally_processed = 0
        detections: list[dict] = []

        h_scale = _H_BINS / 181.0
        s_scale = _S_BINS / 256.0
        v_scale = _V_BINS / 256.0

        # Outer loop over rally intervals; for each rally run _SPAN strided sub-loops.
        # Non-rally frames are skipped entirely — no cache maintenance needed between rallies
        # because each sub-loop bootstraps a fresh cache at its first frame.
        step = 0  # global step counter for pause/progress throttling

        for rally_start, rally_end in rally_intervals:
            for stride_start in range(_SPAN):
                stride_targets = list(range(rally_start + stride_start, rally_end + 1, _SPAN))
                if not stride_targets:
                    continue

                frame_cache: dict[int, np.ndarray] = {}

                for loop_idx, T in enumerate(stride_targets):
                    if loop_idx == 0:
                        # Bootstrap: seed cache with all median-offset and gap-zone frames.
                        for fi in ([T + o for o in _MEDIAN_OFFSETS] +
                                   [T + o for o in _GAP_OFFSETS]):
                            _fetch_frame(cap, fi, frame_cache, video_total_frames)
                    else:
                        # Incremental update: evict the one frame that left the far-negative
                        # end of the median window, read the one frame that entered the far-
                        # positive end. All other needed frames are already in cache.
                        frame_cache.pop((T - _SPAN) + _MEDIAN_OFFSETS[0], None)
                        _fetch_frame(cap, T + _MEDIAN_OFFSETS[-1], frame_cache, video_total_frames)

                    in_rally_processed += 1
                    step += 1
                    frac = in_rally_processed / total_rally_frames if total_rally_frames else 0

                    if step % 30 == 0:
                        progress.check_cancelled()
                        if pause_file.exists():
                            current_fraction = 0.1 + 0.88 * frac
                            (raw_dir / "detections.json").write_text(json.dumps({
                                "stable_frame_count": stable_frame_count,
                                "first_stable_frame": in_frame,
                                "last_stable_frame":  out_frame,
                                "max_ball_radius":    max_ball_radius,
                                "detection_count":    len(detections),
                                "detections":         detections,
                                "paused":             True,
                            }, indent=2))
                            while pause_file.exists():
                                progress.check_cancelled()
                                progress.update(
                                    current_fraction, "paused",
                                    f"Paused at rally frame {in_rally_processed}/{total_rally_frames} — {len(detections)} detections so far",
                                )
                                time.sleep(1.0)

                    if step % 150 == 0:
                        progress.update(
                            0.1 + 0.88 * frac,
                            "detecting",
                            f"Rally frame {in_rally_processed} of {total_rally_frames}…",
                        )

                    frame = frame_cache.get(T)
                    if frame is None:
                        continue

                    # --- Per-frame median background ---
                    median_bgr = _compute_median(frame_cache, T, video_total_frames)
                    if median_bgr is None:
                        continue
                    bg_blur = cv2.medianBlur(median_bgr, 3)

                    # --- Motion mask ---
                    motion_mask = detect_motion(frame, bg_blur, close_kernel)

                    # --- Color mask: vectorized 3D LR_blurred lookup ---
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    h_bin = np.minimum((hsv[:, :, 0] * h_scale).astype(np.int32), _H_BINS - 1)
                    s_bin = np.minimum((hsv[:, :, 1] * s_scale).astype(np.int32), _S_BINS - 1)
                    v_bin = np.minimum((hsv[:, :, 2] * v_scale).astype(np.int32), _V_BINS - 1)
                    color_mask = lr_blurred[h_bin, s_bin, v_bin].astype(np.uint8) * 255
                    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, close_kernel)

                    # --- Combined mask: motion AND color AND tent silhouette ---
                    combined = cv2.bitwise_and(motion_mask, color_mask)
                    combined = cv2.bitwise_and(combined, tent_mask)

                    # --- Blob detection ---
                    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    for contour in contours:
                        (cx, cy), radius = cv2.minEnclosingCircle(contour)
                        area = cv2.contourArea(contour)
                        if min_blob_radius <= radius <= max_blob_radius and area >= 2.0:
                            perimeter = cv2.arcLength(contour, closed=True)
                            detections.append({
                                "frame": T,
                                "cx": round(float(cx), 1),
                                "cy": round(float(cy), 1),
                                "radius": round(float(radius), 1),
                                "area": round(area, 1),
                                "perimeter": round(perimeter, 1),
                            })

                    # --- Annotation patch: 64×64 RGB with R=motion, G=color, B=tent ---
                    # Annotation keys are browser frame numbers (= frame_idx + 1).
                    ann_key = T + 1
                    if ann_key in ann_by_frame:
                        ann = ann_by_frame[ann_key]
                        ax, ay = int(round(ann["x"])), int(round(ann["y"]))
                        sx1, sx2 = max(0, ax - half), min(bg_w, ax + half)
                        sy1, sy2 = max(0, ay - half), min(bg_h, ay + half)
                        dx1 = half - (ax - sx1);  dx2 = dx1 + (sx2 - sx1)
                        dy1 = half - (ay - sy1);  dy2 = dy1 + (sy2 - sy1)
                        patch = np.zeros((half * 2, half * 2, 3), dtype=np.uint8)
                        patch[dy1:dy2, dx1:dx2, 2] = motion_mask[sy1:sy2, sx1:sx2]  # R
                        patch[dy1:dy2, dx1:dx2, 1] = color_mask[sy1:sy2, sx1:sx2]   # G
                        patch[dy1:dy2, dx1:dx2, 0] = tent_mask[sy1:sy2, sx1:sx2]    # B
                        cv2.imwrite(str(patches_dir / f"{ann_key:06d}.png"), patch)

        cap.release()

        # Strides interleave frame order within each rally; restore chronological order.
        detections.sort(key=lambda d: d["frame"])

        # Build a B&W map of all detection locations at tent-mask resolution.
        det_map = np.zeros((bg_h, bg_w), dtype=np.uint8)
        for d in detections:
            cx, cy = int(round(d["cx"])), int(round(d["cy"]))
            x1, x2 = max(0, cx - 1), min(bg_w, cx + 2)
            y1, y2 = max(0, cy - 1), min(bg_h, cy + 2)
            det_map[y1:y2, x1:x2] = 255
        cv2.imwrite(str(raw_dir / "detections_map.png"), det_map)

        result = {
            "stable_frame_count": stable_frame_count,
            "first_stable_frame": in_frame,
            "last_stable_frame":  out_frame,
            "max_ball_radius":    max_ball_radius,
            "detection_count":    len(detections),
            "detections":         detections,
        }
        (raw_dir / "detections.json").write_text(json.dumps(result, indent=2))

        progress.update(1.0, "done", f"Found {len(detections)} candidates in {total_rally_frames} rally frames")
        return result

    def write_raw_outputs(self, ctx: PassContext, result: dict) -> list[dict]:
        path = ctx.paths.pass_raw_dir / "detections.json"
        return [{"role": "raw", "type": "json", "path": str(path)}] if path.exists() else []

    def validate_corrections(self, payload: dict) -> dict:
        # Review/accept workflow not yet defined.
        return payload

    def build_accepted_output(self, ctx: PassContext, raw_result: dict, corrections: dict | None) -> dict:
        # Review/accept workflow not yet defined.
        import shutil
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        src = ctx.paths.pass_raw_dir / "detections.json"
        if src.exists():
            shutil.copy2(src, accepted_dir / "detections.json")
        return raw_result
