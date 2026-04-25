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
            progress.update(0.05 + 0.85 * idx / max(total, 1), "sampling", f"Sampling frame {frame_key}…")

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

        if rows:
            ball_hsv = np.array([(r[3], r[4], r[5]) for r in rows], dtype=np.float32)
            count_s, _ = np.histogramdd(ball_hsv, bins=[_H_EDGES, _S_EDGES, _V_EDGES])
        else:
            count_s = np.zeros((_H_BINS, _S_BINS, _V_BINS))

        mask = count_s > 0
        mask = self._clean_hsvmask(mask)
        np.savez_compressed(raw_dir / "HSVmask.npz", mask=mask)

        progress.update(0.95, "plot", "Writing HSV mask plot…")
        self._write_hsvmask_plot(ctx, mask)

        progress.update(1.0, "done", f"Sampled {len(rows)} ball pixels across {total} annotations")
        return {"ball_pixel_count": len(rows), "annotation_count": total}

    def _clean_hsvmask(self, mask: np.ndarray) -> np.ndarray:
        """Per V-bin: remove isolated single-cell dots, then close gaps with 5×5."""
        from scipy.ndimage import label, binary_closing
        close_struct = np.ones((5, 5), dtype=bool)
        result = np.zeros_like(mask)
        for v in range(_V_BINS):
            sl = mask[:, :, v]
            labeled, _ = label(sl, structure=np.ones((3, 3), dtype=bool))
            sizes = np.bincount(labeled.ravel())
            # Remove components of size 1 (isolated single cells).
            keep = sizes > 1
            keep[0] = False  # background label
            sl = keep[labeled]
            result[:, :, v] = binary_closing(sl, structure=close_struct)
        return result

    def _write_hsvmask_plot(self, ctx: PassContext, mask: np.ndarray) -> None:
        """4×4 grid of H×S subplots (one per V bin) showing signal-present HSV cells."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        h_mids = (np.arange(_H_BINS) + 0.5) * 180.0 / _H_BINS
        s_mids = (np.arange(_S_BINS) + 0.5) * 256.0 / _S_BINS

        fig, axes = plt.subplots(2, 4, figsize=(16, 8))

        for v_idx in range(_V_BINS):
            ax = axes[v_idx // 4, v_idx % 4]
            v_mid = (v_idx + 0.5) * 256.0 / _V_BINS

            hsv_img = np.zeros((_S_BINS, _H_BINS, 3), dtype=np.uint8)
            hsv_img[:, :, 0] = h_mids[np.newaxis, :]
            hsv_img[:, :, 1] = s_mids[:, np.newaxis]
            hsv_img[:, :, 2] = int(v_mid)
            rgb_img = cv2.cvtColor(hsv_img, cv2.COLOR_HSV2RGB)

            ax.imshow(rgb_img, origin="lower", extent=[0, 180, 0, 255],
                      aspect="auto", interpolation="nearest")

            dot_color = "white" if v_mid < 128 else "black"
            h_idx, s_idx = np.where(mask[:, :, v_idx])
            if h_idx.size:
                ax.scatter(h_mids[h_idx], s_mids[s_idx],
                           s=4, c=dot_color, linewidths=0)

            ax.set_title(f"V bin {v_idx}  (V≈{v_mid:.0f})", fontsize=8)
            ax.set_xlabel("H", fontsize=7)
            ax.set_ylabel("S", fontsize=7)
            ax.tick_params(labelsize=6)

        fig.suptitle("HSV Mask — cells with at least one ball pixel", fontsize=11)
        fig.tight_layout()
        fig.savefig(str(ctx.paths.pass_raw_dir / "HSVmask.png"), dpi=120)
        plt.close(fig)

    def write_raw_outputs(self, ctx: PassContext, result: dict) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = []
        for name, typ in [
            ("ball_colors.csv", "csv"),
            ("HSVmask.npz", "npz"), ("HSVmask.png", "png"),
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
        for name in ("ball_colors.csv", "HSVmask.npz", "HSVmask.png"):
            src = ctx.paths.pass_raw_dir / name
            if src.exists():
                shutil.copy2(src, accepted_dir / name)
        return raw_result
