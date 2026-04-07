"""Pass 3 — Ball Color Tagging: extract per-pixel RGB+HSV samples from annotated balls."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import cv2
import numpy as np

from pbva_core.types import Pass2AcceptedOutput
from pbva_pipeline.base import PassContext


class Pass3:
    name = "pass3"

    def validate_inputs(self, ctx: PassContext) -> None:
        if not ctx.prior_accepted:
            raise ValueError("Pass 2 accepted output is required for Pass 3")
        patches_dir = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "patches" / "raw"
        if not patches_dir.exists():
            raise FileNotFoundError(f"Pass 2 accepted patches not found: {patches_dir}")
        ann_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "annotations.json"
        if not ann_path.exists():
            raise FileNotFoundError(f"Pass 2 annotations not found: {ann_path}")

    def run(self, ctx: PassContext, progress=None):
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.05, "setup", "Reading Pass 2 accepted output…")
        p2 = Pass2AcceptedOutput.model_validate(ctx.prior_accepted)
        rmin = p2.min_ball_radius

        ann_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "annotations.json"
        annotations = json.loads(ann_path.read_text()).get("annotations", {})

        patches_dir = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "patches" / "raw"
        patch_files = {str(int(p.stem)): p for p in patches_dir.glob("*.png")}

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        csv_path = raw_dir / "ball_colors.csv"

        total = len(annotations)
        rows = []

        for idx, (frame_key, ann) in enumerate(annotations.items()):
            progress.update(0.05 + 0.9 * idx / max(total, 1), "sampling", f"Sampling frame {frame_key}…")

            patch_path = patch_files.get(frame_key)
            if patch_path is None:
                continue

            bgr = cv2.imread(str(patch_path))
            if bgr is None:
                continue

            h, w = bgr.shape[:2]
            cx, cy = w / 2.0, h / 2.0

            # Use the per-annotation radius if available, otherwise fall back to rmin.
            ann_radius = ann.get("radius", 0) if isinstance(ann, dict) else 0
            sample_radius = ann_radius if ann_radius > 0 else rmin

            # Convert entire patch to HSV once (H: 0–180, S: 0–255, V: 0–255).
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

            for py in range(h):
                for px in range(w):
                    dist = math.sqrt((px - cx) ** 2 + (py - cy) ** 2)
                    if dist <= sample_radius:
                        b, g, r = int(bgr[py, px, 0]), int(bgr[py, px, 1]), int(bgr[py, px, 2])
                        hv, sv, vv = int(hsv[py, px, 0]), int(hsv[py, px, 1]), int(hsv[py, px, 2])
                        rows.append((r, g, b, hv, sv, vv))

        progress.update(0.90, "writing", "Writing ball_colors.csv…")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["R", "G", "B", "H", "S", "V"])
            writer.writerows(rows)

        progress.update(0.93, "bg", "Writing bg_colors.csv…")
        bg_pixel_count = self._write_bg_colors(ctx)

        progress.update(0.95, "plot", "Writing scatter plots…")
        self._write_scatter_plot(ctx, rows, x_col="H", y_col="S",
                                 x_label="Hue (0–180)", y_label="Saturation (0–255)",
                                 x_lim=(0, 180), y_lim=(0, 255),
                                 ball_x_idx=3, ball_y_idx=4,
                                 stem="hue_saturation")
        self._write_scatter_plot(ctx, [], x_col="H", y_col="S",
                                 x_label="Hue (0–180)", y_label="Saturation (0–255)",
                                 x_lim=(0, 180), y_lim=(0, 255),
                                 ball_x_idx=3, ball_y_idx=4,
                                 stem="hue_saturation_bg")
        self._write_scatter_plot(ctx, rows, x_col="V", y_col="S",
                                 x_label="Value (0–255)", y_label="Saturation (0–255)",
                                 x_lim=(0, 255), y_lim=(0, 255),
                                 ball_x_idx=5, ball_y_idx=4,
                                 stem="value_saturation")
        self._write_scatter_plot(ctx, [], x_col="V", y_col="S",
                                 x_label="Value (0–255)", y_label="Saturation (0–255)",
                                 x_lim=(0, 255), y_lim=(0, 255),
                                 ball_x_idx=5, ball_y_idx=4,
                                 stem="value_saturation_bg")

        progress.update(1.0, "done", f"Sampled {len(rows)} ball pixels, {bg_pixel_count} bg pixels")
        return {"ball_pixel_count": len(rows), "bg_pixel_count": bg_pixel_count, "annotation_count": total}

    def _write_scatter_plot(self, ctx: PassContext, ball_rows: list,
                            x_col: str, y_col: str,
                            x_label: str, y_label: str,
                            x_lim: tuple, y_lim: tuple,
                            ball_x_idx: int, ball_y_idx: int,
                            stem: str) -> None:
        """Scatter plot of two color channels: bg pixels in gray, ball pixels in true RGB."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        raw_dir = ctx.paths.pass_raw_dir

        # Load bg points for the requested channels.
        bg_csv = raw_dir / "bg_colors.csv"
        bg_x, bg_y = [], []
        if bg_csv.exists():
            with open(bg_csv, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    bg_x.append(int(row[x_col]))
                    bg_y.append(int(row[y_col]))

        fig, ax = plt.subplots(figsize=(8, 6))

        _BG_CAP = 20_000
        if bg_x:
            if len(bg_x) > _BG_CAP:
                rng = np.random.default_rng(0)
                idx = rng.choice(len(bg_x), _BG_CAP, replace=False)
                bg_x = [bg_x[i] for i in idx]
                bg_y = [bg_y[i] for i in idx]
            ax.scatter(bg_x, bg_y, s=16, marker="s", color=(0.6, 0.6, 0.6), alpha=0.20, linewidths=0, label="background")

        if ball_rows:
            ball_x = [r[ball_x_idx] for r in ball_rows]
            ball_y = [r[ball_y_idx] for r in ball_rows]
            ball_rgb = [(r[0] / 255, r[1] / 255, r[2] / 255) for r in ball_rows]
            ax.scatter(ball_x, ball_y, s=4, c=ball_rgb, alpha=1.0, linewidths=0, label="ball")

        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_xlim(*x_lim)
        ax.set_ylim(*y_lim)
        ax.legend(markerscale=4, loc="upper right")
        fig.tight_layout()
        fig.savefig(str(raw_dir / f"{stem}.png"), dpi=150)

        # Extract axes bounding box in image pixel coordinates (origin top-left).
        renderer = fig.canvas.get_renderer()
        bbox = ax.get_window_extent(renderer)
        fig_w_px, fig_h_px = fig.canvas.get_width_height()
        mapping = {
            "image_width":  fig_w_px,
            "image_height": fig_h_px,
            "axes_left":    round(bbox.x0),
            "axes_right":   round(bbox.x1),
            "axes_top":     round(fig_h_px - bbox.y1),
            "axes_bottom":  round(fig_h_px - bbox.y0),
            f"{x_col.lower()}_min": x_lim[0],
            f"{x_col.lower()}_max": x_lim[1],
            f"{y_col.lower()}_min": y_lim[0],
            f"{y_col.lower()}_max": y_lim[1],
        }
        (raw_dir / f"{stem}.json").write_text(json.dumps(mapping, indent=2))
        plt.close(fig)

    def _write_bg_colors(self, ctx: PassContext) -> int:
        """Sample all tent-masked pixels from the median background plate into bg_colors.csv."""
        pass1_dir = ctx.paths.project_root / "passes" / "pass1"
        p1_result_path = pass1_dir / "raw" / "result.json"
        if not p1_result_path.exists():
            return 0
        p1_result = json.loads(p1_result_path.read_text())
        median_paths = p1_result.get("median_background_paths", [])
        if not median_paths:
            return 0
        bg_path = ctx.paths.project_root / median_paths[0]
        mask_path = pass1_dir / "accepted" / "tent_mask.png"

        bgr = cv2.imread(str(bg_path))
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if bgr is None or mask is None:
            return 0

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        ys, xs = np.where(mask > 0)
        b = bgr[ys, xs, 0]
        g = bgr[ys, xs, 1]
        r = bgr[ys, xs, 2]
        h = hsv[ys, xs, 0]
        s = hsv[ys, xs, 1]
        v = hsv[ys, xs, 2]

        csv_path = ctx.paths.pass_raw_dir / "bg_colors.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["R", "G", "B", "H", "S", "V"])
            writer.writerows(zip(r.tolist(), g.tolist(), b.tolist(), h.tolist(), s.tolist(), v.tolist()))

        return int(len(ys))

    def write_raw_outputs(self, ctx: PassContext, result: dict) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = []
        for name, typ in [
            ("ball_colors.csv", "csv"), ("bg_colors.csv", "csv"),
            ("hue_saturation.png", "png"), ("hue_saturation.json", "json"),
            ("hue_saturation_bg.png", "png"),
            ("value_saturation.png", "png"), ("value_saturation.json", "json"),
            ("value_saturation_bg.png", "png"),
        ]:
            p = raw_dir / name
            if p.exists():
                artifacts.append({"role": "raw", "type": typ, "path": str(p)})
        return artifacts

    def validate_corrections(self, payload: dict) -> dict:
        for key in ("hue_saturation", "value_saturation"):
            verts = payload.get(key, [])
            if not isinstance(verts, list):
                raise ValueError(f"{key} must be a list")
            for v in verts:
                if not (isinstance(v, list) and len(v) == 2
                        and all(isinstance(c, (int, float)) for c in v)):
                    raise ValueError(f"{key} vertices must be [x, y] numeric pairs")
        return payload

    def build_accepted_output(self, ctx: PassContext, raw_result: dict, corrections: dict | None) -> dict:
        import shutil
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        for name in ("ball_colors.csv", "bg_colors.csv",
                     "hue_saturation.png", "hue_saturation.json", "hue_saturation_bg.png",
                     "value_saturation.png", "value_saturation.json", "value_saturation_bg.png"):
            src = ctx.paths.pass_raw_dir / name
            if src.exists():
                shutil.copy2(src, accepted_dir / name)
        if corrections:
            (accepted_dir / "ball_color_polygons.json").write_text(
                json.dumps(corrections, indent=2)
            )
        return raw_result
