"""Pass 4 — Ball Detection: per-frame motion+color+silhouette mask over the stable video range."""

from __future__ import annotations

import bisect
import json
import time
from pathlib import Path

import cv2
import numpy as np

from pbva_core.types import Pass1AcceptedOutput, Pass1RawResult
from pbva_pipeline.base import PassContext


def _select_bg_index(frame_idx: int, fps: float, window_times: list) -> int:
    """Return the index of the median background whose midpoint is closest to frame_idx."""
    if len(window_times) <= 1:
        return 0
    t = frame_idx / fps
    best, best_dist = 0, float('inf')
    for k, (ws, we) in enumerate(window_times):
        dist = abs((ws + we) / 2 - t)
        if dist < best_dist:
            best_dist = dist
            best = k
    return best


def detect_motion(frame, bg_blur, close_kernel, blur=3, threshold=25):
    frame_blur = cv2.medianBlur(frame, blur)
    diff = cv2.absdiff(frame_blur, bg_blur)
    motion = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, moving = cv2.threshold(motion, threshold, 255, cv2.THRESH_BINARY)
    moving = cv2.medianBlur(moving, 3)
    solid  = cv2.morphologyEx(moving, cv2.MORPH_CLOSE, close_kernel)
    return solid


def _build_color_lut(polygon: list, lut_h: int, lut_w: int) -> np.ndarray:
    """Rasterise a data-space polygon into a (lut_h × lut_w) binary LUT.

    Each polygon vertex is [x, y] in data coordinates, used directly as pixel
    coordinates (x → column, y → row) in the LUT image.
    """
    lut = np.zeros((lut_h, lut_w), dtype=np.uint8)
    if len(polygon) >= 3:
        pts = np.array([[int(round(p[0])), int(round(p[1]))] for p in polygon], dtype=np.int32)
        cv2.fillPoly(lut, [pts], 255)
    return lut


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
        bg_path = ctx.paths.project_root / "passes" / "pass1" / "raw" / "median_background_0.png"
        if not bg_path.exists():
            raise FileNotFoundError(f"Median background not found: {bg_path}")
        p2_result = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "result.json"
        if not p2_result.exists():
            raise FileNotFoundError("Pass 2 accepted result.json not found")
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        if not rally_path.exists():
            raise FileNotFoundError("Pass 2 accepted rally.json not found — record rallies in Pass 2 first")
        poly_path = ctx.paths.project_root / "passes" / "pass3" / "accepted" / "ball_color_polygons.json"
        if not poly_path.exists():
            raise FileNotFoundError("Pass 3 ball_color_polygons.json not found — accept Pass 3 first")

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

        progress.update(0.03, "setup", "Loading pass 1 raw result…")
        p1_raw = Pass1RawResult.model_validate_json(
            (pass1_dir / "raw" / "result.json").read_text()
        )

        progress.update(0.04, "setup", "Loading background plates and tent mask…")
        bg_blurs: list[np.ndarray] = []
        for rel_path in p1_raw.median_background_paths:
            plate = cv2.imread(str(ctx.paths.project_root / rel_path))
            bg_blurs.append(cv2.medianBlur(plate, 3))
        window_times = p1_raw.median_window_times
        bg_plate = cv2.imread(str(ctx.paths.project_root / p1_raw.median_background_paths[0]))
        tent_mask = cv2.imread(str(pass1_dir / "accepted" / "tent_mask.png"), cv2.IMREAD_GRAYSCALE)

        progress.update(0.05, "setup", "Loading ball radius, annotations, and rally bounds from pass 2…")
        p2_result = json.loads(
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "result.json").read_text()
        )
        max_ball_radius = p2_result.get("max_ball_radius", 16)
        min_blob_radius = p2_result.get("min_ball_radius", 4) / 4

        # Load rally bounds; rally frame numbers are browser-side (OpenCV frame_idx + 1).
        rally_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json"
        rally_intervals: list[tuple[int, int]] = sorted(
            (r["start_frame"] - 1, r["stop_frame"] - 1)
            for r in json.loads(rally_path.read_text()).get("rally", [])
        )
        rally_starts = [s for s, _ in rally_intervals]
        rally_stops  = [e for _, e in rally_intervals]

        ann_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "annotations.json"
        ann_by_frame: dict[int, dict] = {}
        if ann_path.exists():
            raw = json.loads(ann_path.read_text()).get("annotations", {})
            ann_by_frame = {int(k): v for k, v in raw.items()}

        progress.update(0.06, "setup", "Building color lookup tables from pass 3 polygons…")
        poly_path = ctx.paths.project_root / "passes" / "pass3" / "accepted" / "ball_color_polygons.json"
        polygons  = json.loads(poly_path.read_text())

        # Hue-Saturation LUT: rows=S (0-255), cols=H (0-180).
        hs_lut = _build_color_lut(polygons.get("hue_saturation", []), lut_h=256, lut_w=181)
        # Value-Saturation LUT: rows=S (0-255), cols=V (0-255).
        vs_lut = _build_color_lut(polygons.get("value_saturation", []), lut_h=256, lut_w=256)

        progress.update(0.08, "setup", "Opening video…")
        cap = cv2.VideoCapture(str(ctx.video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {ctx.video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or ctx.video_fps
        in_frame  = max(0, int(in_time_s  * fps))
        out_frame = int(out_time_s * fps)
        total_frames = max(1, out_frame - in_frame + 1)

        cap.set(cv2.CAP_PROP_POS_FRAMES, in_frame)

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        pause_file  = raw_dir / ".pause"
        patches_dir = raw_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        close_kernel = np.ones((5, 5), np.uint8)
        max_blob_radius = max_ball_radius + close_kernel.shape[0]
        bg_h, bg_w   = bg_plate.shape[:2]
        half = 32   # patch half-size → 64×64 output
        total_rally_frames = sum(e - s + 1 for s, e in rally_intervals)
        stable_frame_count = 0
        in_rally_processed = 0
        detections = []

        for i in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break

            # Use the actual post-read position to determine which frame was
            # just decoded.  After cap.read(), CAP_PROP_POS_FRAMES points to
            # the *next* frame, so the frame we hold is one behind that.
            frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1

            stable_frame_count += 1

            # Skip detection for frames outside all recorded rally bounds.
            rally_idx = bisect.bisect_right(rally_starts, frame_idx) - 1
            in_rally = rally_idx >= 0 and frame_idx <= rally_stops[rally_idx]
            if in_rally:
                in_rally_processed += 1

            frac = in_rally_processed / total_rally_frames if total_rally_frames else 0

            # Check for pause/cancel signal every 30 frames (~1 s at 30 fps).
            if i % 30 == 0:
                progress.check_cancelled()
                if pause_file.exists():
                    current_fraction = 0.1 + 0.88 * frac
                    # Write partial results so the UI can show them while paused.
                    (raw_dir / "detections.json").write_text(json.dumps({
                        "stable_frame_count": stable_frame_count,
                        "first_stable_frame": in_frame,
                        "last_stable_frame":  frame_idx - 1,
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

            if i % 150 == 0:
                progress.update(
                    0.1 + 0.88 * frac,
                    "detecting",
                    f"Rally frame {in_rally_processed} of {total_rally_frames}…",
                )

            if not in_rally:
                continue

            # --- Motion mask (use temporally closest median background) ---
            bg_blur = bg_blurs[_select_bg_index(frame_idx, fps, window_times)]
            motion_mask = detect_motion(frame, bg_blur, close_kernel)

            # --- H-S and V-S color masks (each cleaned separately) ---
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            H = hsv[:, :, 0]   # 0-180
            S = hsv[:, :, 1]   # 0-255
            V = hsv[:, :, 2]   # 0-255
            hs_mask = cv2.morphologyEx((hs_lut[S, H] > 0).astype(np.uint8) * 255, cv2.MORPH_CLOSE, close_kernel)
            vs_mask = cv2.morphologyEx((vs_lut[S, V] > 0).astype(np.uint8) * 255, cv2.MORPH_CLOSE, close_kernel)

            # --- Combined mask: motion AND H-S AND V-S AND tent silhouette ---
            combined = cv2.bitwise_and(motion_mask, hs_mask)
            combined = cv2.bitwise_and(combined, vs_mask)
            combined = cv2.bitwise_and(combined, tent_mask)

            # --- Blob detection ---
            contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                (cx, cy), radius = cv2.minEnclosingCircle(contour)
                area = cv2.contourArea(contour)
                if min_blob_radius <= radius <= max_blob_radius and area >= 2.0:
                    perimeter = cv2.arcLength(contour, closed=True)
                    detections.append({
                        "frame": frame_idx,
                        "cx": round(float(cx), 1),
                        "cy": round(float(cy), 1),
                        "radius": round(float(radius), 1),
                        "area": round(area, 1),
                        "perimeter": round(perimeter, 1),
                    })

            # --- Annotation patch: 64×64 RGB showing R=motion, G=H-S mask, B=V-S mask ---
            # Annotation keys are browser frame numbers (= frame_idx + 1 due to PTS offset).
            ann_key = frame_idx + 1
            if ann_key in ann_by_frame:
                ann = ann_by_frame[ann_key]
                ax, ay = int(round(ann["x"])), int(round(ann["y"]))
                sx1, sx2 = max(0, ax - half), min(bg_w, ax + half)
                sy1, sy2 = max(0, ay - half), min(bg_h, ay + half)
                dx1 = half - (ax - sx1);  dx2 = dx1 + (sx2 - sx1)
                dy1 = half - (ay - sy1);  dy2 = dy1 + (sy2 - sy1)
                patch = np.zeros((half * 2, half * 2, 3), dtype=np.uint8)
                patch[dy1:dy2, dx1:dx2, 2] = motion_mask[sy1:sy2, sx1:sx2]  # R
                patch[dy1:dy2, dx1:dx2, 1] = hs_mask[sy1:sy2, sx1:sx2]      # G
                patch[dy1:dy2, dx1:dx2, 0] = vs_mask[sy1:sy2, sx1:sx2]      # B
                cv2.imwrite(str(patches_dir / f"{ann_key:06d}.png"), patch)

        cap.release()

        # Build a B&W map of all detection locations at bg-plate resolution.
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
            "last_stable_frame":  in_frame + stable_frame_count - 1,
            "max_ball_radius":    max_ball_radius,
            "detection_count":    len(detections),
            "detections":         detections,
        }
        (raw_dir / "detections.json").write_text(json.dumps(result, indent=2))

        progress.update(1.0, "done", f"Found {len(detections)} candidates in {stable_frame_count} frames")
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
