"""Pass 3 — Ball Color Tagging: extract per-pixel RGB+HSV samples from annotated balls."""

from __future__ import annotations

import csv
import json
import math

import cv2
import numpy as np

from pbva_core.types import Pass2AcceptedOutput
from pbva_pipeline.base import PassContext

_H_BINS, _S_BINS, _V_BINS = 48, 48, 8
_H_EDGES = np.linspace(0, 181, _H_BINS + 1)   # 181 ensures H=180 falls in last bin
_S_EDGES = np.linspace(0, 256, _S_BINS + 1)   # 256 ensures S=255 falls in last bin
_V_EDGES = np.linspace(0, 256, _V_BINS + 1)


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
            progress.update(0.05 + 0.10 * idx / max(total, 1), "sampling", f"Sampling frame {frame_key}…")

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

        progress.update(0.15, "writing", "Writing ball_colors.csv…")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["R", "G", "B", "H", "S", "V"])
            writer.writerows(rows)

        if rows:
            ball_hsv = np.array([(r[3], r[4], r[5]) for r in rows], dtype=np.float32)
            count_s, _ = np.histogramdd(ball_hsv, bins=[_H_EDGES, _S_EDGES, _V_EDGES])
        else:
            count_s = np.zeros((_H_BINS, _S_BINS, _V_BINS))

        bg_pixel_count, count_b = self._write_bg_colors(ctx, annotations, progress, start=0.15, end=0.90)

        alpha = 0.1
        total_bins = _H_BINS * _S_BINS * _V_BINS
        N_s = float(count_s.sum())
        N_b = float(count_b.sum())
        p_s = (count_s + alpha) / (N_s + alpha * total_bins)
        p_b = (count_b + alpha) / (N_b + alpha * total_bins)
        LR = p_s / p_b
        # LR for a bin with no data in either histogram equals this floor value.
        # Bins above this floor are genuinely signal-enriched.
        lr_floor = (N_b + alpha * total_bins) / (N_s + alpha * total_bins)

        np.savez_compressed(ctx.paths.pass_raw_dir / "Pratio.npz",
                            lr_ratio=(LR / lr_floor).astype(np.float32))

        progress.update(0.90, "pratio", "Writing likelihood ratio plot…")
        self._write_pratio_plot(ctx, LR, lr_floor)

        progress.update(1.0, "done", f"Sampled {len(rows)} ball pixels, {bg_pixel_count} background pixels")
        return {"ball_pixel_count": len(rows), "bg_pixel_count": bg_pixel_count, "annotation_count": total}

    def _write_bg_colors(self, ctx: PassContext, annotations: dict, progress,
                         start: float = 0.15, end: float = 0.90,
                         span: int = 5, gap: int = 15, n_side: int = 15) -> int:
        """Sample tent-masked pixels from a per-annotation local median into bg_colors.csv.

        For each annotated frame F, computes a median image from frames at offsets
        ±(gap + k*span) for k in 0..n_side-1 relative to F, skipping out-of-bounds frames.
        All masked pixels from every such median image are accumulated into the CSV.
        """
        mask_path = ctx.paths.project_root / "passes" / "pass1" / "accepted" / "tent_mask.png"
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            return 0

        offsets = sorted([-gap - k * span for k in range(n_side)] + [gap + k * span for k in range(n_side)])
        total_frames = int(ctx.video_fps * ctx.video_duration_s)
        ann_keys = list(annotations.keys())
        total = len(ann_keys)

        cap = cv2.VideoCapture(str(ctx.video_path))
        try:
            ys, xs = np.where(mask > 0)
            all_rows: list[np.ndarray] = []

            for idx, frame_key in enumerate(ann_keys):
                progress.update(start + (end - start) * idx / max(total, 1),
                                 "bg", f"Background median for frame {frame_key}…")
                # frame_key is a browser frame number; OpenCV index = frame_key - 1
                center = int(frame_key) - 1
                sample_indices = [center + o for o in offsets if 0 <= center + o < total_frames]
                if not sample_indices:
                    continue

                frames: list[np.ndarray] = []
                for fi in sample_indices:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                    ok, frame = cap.read()
                    if ok:
                        frames.append(frame)

                if not frames:
                    continue

                median_bgr = np.median(np.stack(frames, axis=0), axis=0).astype(np.uint8)
                median_hsv = cv2.cvtColor(median_bgr, cv2.COLOR_BGR2HSV)

                b = median_bgr[ys, xs, 0]
                g = median_bgr[ys, xs, 1]
                r = median_bgr[ys, xs, 2]
                h = median_hsv[ys, xs, 0]
                s = median_hsv[ys, xs, 1]
                v = median_hsv[ys, xs, 2]

                all_rows.append(np.stack([h, s, v], axis=1))
        finally:
            cap.release()

        if all_rows:
            hsv_array = np.concatenate(all_rows, axis=0)
        else:
            hsv_array = np.empty((0, 3), dtype=np.uint8)

        np.savez_compressed(ctx.paths.pass_raw_dir / "bg_colors.npz", hsv=hsv_array)

        if len(hsv_array):
            count_b, _ = np.histogramdd(hsv_array.astype(np.float32),
                                        bins=[_H_EDGES, _S_EDGES, _V_EDGES])
        else:
            count_b = np.zeros((_H_BINS, _S_BINS, _V_BINS))

        return len(hsv_array), count_b

    def _write_pratio_plot(self, ctx: PassContext, LR: np.ndarray, lr_floor: float) -> None:
        """4×4 grid of H×S subplots (one per V bin) showing LR colored by HSV midpoint."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        h_mids = (np.arange(_H_BINS) + 0.5) * 180.0 / _H_BINS   # shape (64,)
        s_mids = (np.arange(_S_BINS) + 0.5) * 256.0 / _S_BINS   # shape (64,)

        from scipy.ndimage import binary_dilation
        struct = np.ones((3, 3, 1), dtype=bool)   # 8-connected in H×S, isolated in V
        LR_blurred = binary_dilation(LR > lr_floor, structure=struct)
        np.savez_compressed(ctx.paths.pass_raw_dir / "Pratio_mask.npz", mask=LR_blurred)

        fig, axes = plt.subplots(2, 4, figsize=(16, 8))

        for v_idx in range(_V_BINS):
            ax = axes[v_idx // 4, v_idx % 4]
            v_mid = (v_idx + 0.5) * 256.0 / _V_BINS

            # Build background image: rgb_img[s_idx, h_idx] = HSV midpoint colour.
            hsv_img = np.zeros((_S_BINS, _H_BINS, 3), dtype=np.uint8)
            hsv_img[:, :, 0] = h_mids[np.newaxis, :]   # H varies along columns (x)
            hsv_img[:, :, 1] = s_mids[:, np.newaxis]   # S varies along rows (y)
            hsv_img[:, :, 2] = int(v_mid)
            rgb_img = cv2.cvtColor(hsv_img, cv2.COLOR_HSV2RGB)

            ax.imshow(rgb_img, origin="lower", extent=[0, 180, 0, 255],
                      aspect="auto", interpolation="nearest")

            # Mark each (H, S) cell whose blurred LR exceeds the floor with a small dot.
            dot_color = "white" if v_mid < 128 else "black"
            h_idx, s_idx = np.where(LR_blurred[:, :, v_idx])
            if h_idx.size:
                ax.scatter(h_mids[h_idx], s_mids[s_idx],
                           s=4, c=dot_color, linewidths=0)

            ax.set_title(f"V bin {v_idx}  (V≈{v_mid:.0f})", fontsize=8)
            ax.set_xlabel("H", fontsize=7)
            ax.set_ylabel("S", fontsize=7)
            ax.tick_params(labelsize=6)

        fig.suptitle("Likelihood Ratio  P(H,S,V | ball) / P(H,S,V | background)", fontsize=11)
        fig.tight_layout()
        fig.savefig(str(ctx.paths.pass_raw_dir / "Pratio.png"), dpi=120)
        plt.close(fig)

    def write_raw_outputs(self, ctx: PassContext, result: dict) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = []
        for name, typ in [
            ("ball_colors.csv", "csv"), ("bg_colors.npz", "npz"),
            ("Pratio.npz", "npz"), ("Pratio_mask.npz", "npz"), ("Pratio.png", "png"),
        ]:
            p = raw_dir / name
            if p.exists():
                artifacts.append({"role": "raw", "type": typ, "path": str(p)})
        return artifacts

    def validate_corrections(self, payload: dict) -> dict:
        return {}

    def build_accepted_output(self, ctx: PassContext, raw_result: dict, corrections: dict | None) -> dict:
        import shutil
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        for name in ("ball_colors.csv", "bg_colors.npz", "Pratio.npz", "Pratio_mask.npz", "Pratio.png"):
            src = ctx.paths.pass_raw_dir / name
            if src.exists():
                shutil.copy2(src, accepted_dir / name)
        return raw_result
