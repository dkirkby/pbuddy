"""Pass 4 — Ball Detection: per-frame motion+color+silhouette mask over the stable video range."""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

from pbva_core.types import Pass1AcceptedOutput
from pbva_pipeline.base import PassContext


def detect_motion(frame, bg_blur, open_kernel, close_kernel, blur=5, threshold=25):
    frame_blur = cv2.medianBlur(frame, blur)
    diff = cv2.absdiff(frame_blur, bg_blur)
    motion = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, moving = cv2.threshold(motion, threshold, 255, cv2.THRESH_BINARY)
    cleaned = cv2.morphologyEx(moving,  cv2.MORPH_OPEN,  open_kernel)
    solid   = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, close_kernel)
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
        bg_path = ctx.paths.project_root / "passes" / "pass1" / "raw" / "median_background.png"
        if not bg_path.exists():
            raise FileNotFoundError(f"Median background not found: {bg_path}")
        p2_result = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "result.json"
        if not p2_result.exists():
            raise FileNotFoundError("Pass 2 accepted result.json not found")
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

        progress.update(0.04, "setup", "Loading background plate and tent mask…")
        bg_plate = cv2.imread(str(pass1_dir / "raw" / "median_background.png"))
        tent_mask = cv2.imread(str(pass1_dir / "accepted" / "tent_mask.png"), cv2.IMREAD_GRAYSCALE)
        bg_blur   = cv2.medianBlur(bg_plate, 5)

        progress.update(0.05, "setup", "Loading max ball radius from pass 2…")
        p2_result = json.loads(
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "result.json").read_text()
        )
        max_ball_radius = p2_result.get("max_ball_radius", 16)
        min_blob_radius = p2_result.get("min_ball_radius", 4) / 4

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

        pause_file = raw_dir / ".pause"
        open_kernel  = np.ones((5, 5), np.uint8)
        close_kernel = np.ones((9, 9), np.uint8)
        stable_frame_count = 0
        detections = []

        for i in range(total_frames):
            ret, frame = cap.read()
            if not ret:
                break

            # Use the actual post-read position to determine which frame was
            # just decoded.  After cap.read(), CAP_PROP_POS_FRAMES points to
            # the *next* frame, so the frame we hold is one behind that.
            frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1

            # Check for pause signal every 30 frames (~1 s at 30 fps).
            if i % 30 == 0 and pause_file.exists():
                current_fraction = 0.1 + 0.88 * i / total_frames
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
                    progress.update(
                        current_fraction, "paused",
                        f"Paused at frame {frame_idx} — {len(detections)} detections so far",
                    )
                    time.sleep(1.0)

            if i % 150 == 0:
                progress.update(
                    0.1 + 0.88 * i / total_frames,
                    "detecting",
                    f"Frame {frame_idx} of {out_frame}…",
                )

            # --- Motion mask ---
            motion_mask = detect_motion(frame, bg_blur, open_kernel, close_kernel)

            # --- Color mask ---
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            H = hsv[:, :, 0]   # 0-180
            S = hsv[:, :, 1]   # 0-255
            V = hsv[:, :, 2]   # 0-255
            color_raw = ((hs_lut[S, H] > 0) & (vs_lut[S, V] > 0)).astype(np.uint8) * 255
            color_mask = cv2.morphologyEx(color_raw,  cv2.MORPH_OPEN,  open_kernel)
            color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, close_kernel)

            # --- Combined mask: motion AND color AND tent silhouette ---
            combined = cv2.bitwise_and(motion_mask, color_mask)
            combined = cv2.bitwise_and(combined, tent_mask)

            # --- Blob detection ---
            contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                (cx, cy), radius = cv2.minEnclosingCircle(contour)
                if min_blob_radius <= radius <= max_ball_radius:
                    area = cv2.contourArea(contour)
                    perimeter = cv2.arcLength(contour, closed=True)
                    detections.append({
                        "frame": frame_idx,
                        "cx": round(float(cx), 1),
                        "cy": round(float(cy), 1),
                        "radius": round(float(radius), 1),
                        "area": round(area, 1),
                        "perimeter": round(perimeter, 1),
                    })

            stable_frame_count += 1

        cap.release()

        # Build a B&W map of all detection locations at bg-plate resolution.
        bg_h, bg_w = bg_plate.shape[:2]
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
