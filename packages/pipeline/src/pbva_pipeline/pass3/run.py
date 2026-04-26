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
        ann_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "annotations.json"
        if not ann_path.exists():
            raise FileNotFoundError(f"Pass 2 annotations not found: {ann_path}")
        annotations = json.loads(ann_path.read_text()).get("annotations", {})
        if annotations:
            patches_dir = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "patches" / "raw"
            if not patches_dir.exists():
                raise FileNotFoundError(f"Pass 2 accepted patches not found: {patches_dir}")

    def run(self, ctx: PassContext, progress=None, nbg_nsig_ratio: int = 100):
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.05, "setup", "Reading Pass 2 accepted output…")
        p2 = Pass2AcceptedOutput.model_validate(ctx.prior_accepted)
        rmin = p2.min_ball_radius

        ann_path = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "annotations.json"
        annotations = json.loads(ann_path.read_text()).get("annotations", {})

        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        if not annotations:
            result = {"annotation_count": 0, "ball_pixel_count": 0,
                      "min_ball_radius": p2.min_ball_radius, "max_ball_radius": p2.max_ball_radius}
            (raw_dir / "result.json").write_text(json.dumps(result, indent=2))
            progress.update(1.0, "done", "No ball annotations — mask will be borrowed from another project")
            return result

        patches_dir = ctx.paths.project_root / "passes" / "pass2" / "accepted" / "patches" / "raw"
        patch_files = {str(int(p.stem)): p for p in patches_dir.glob("*.png")}

        csv_path = raw_dir / "ball_colors.csv"

        total = len(annotations)
        rows = []
        n_sig_per_frame: dict[str, int] = {}

        for idx, (frame_key, ann) in enumerate(annotations.items()):
            progress.update(0.05 + 0.40 * idx / max(total, 1), "sampling", f"Sampling frame {frame_key}…")

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

            n_sig = 0
            for py in range(h):
                for px in range(w):
                    dist = math.sqrt((px - cx) ** 2 + (py - cy) ** 2)
                    if dist <= sample_radius:
                        b, g, r = int(bgr[py, px, 0]), int(bgr[py, px, 1]), int(bgr[py, px, 2])
                        hv, sv, vv = int(hsv[py, px, 0]), int(hsv[py, px, 1]), int(hsv[py, px, 2])
                        rows.append((r, g, b, hv, sv, vv))
                        n_sig += 1
            n_sig_per_frame[frame_key] = n_sig

        progress.update(0.45, "writing", "Writing ball_colors.csv…")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["R", "G", "B", "H", "S", "V"])
            writer.writerows(rows)

        if rows:
            ball_hsv = np.array([(r[3], r[4], r[5]) for r in rows], dtype=np.float32)
            count_s, _ = np.histogramdd(ball_hsv, bins=[_H_EDGES, _S_EDGES, _V_EDGES])
        else:
            count_s = np.zeros((_H_BINS, _S_BINS, _V_BINS))

        progress.update(0.50, "bg", "Sampling background pixels…")
        count_b = self._sample_bg_colors(ctx, annotations, n_sig_per_frame, rmin, progress,
                                         start=0.50, end=0.88, nbg_nsig_ratio=nbg_nsig_ratio)

        from scipy.ndimage import gaussian_filter
        count_s_smooth = gaussian_filter(count_s, sigma=(0.5, 0.5, 0))
        count_b_smooth = gaussian_filter(count_b, sigma=(0.5, 0.5, 0))
        prob_s = count_s_smooth / max(count_s_smooth.sum(), 1.0)
        prob_b = count_b_smooth / max(count_b_smooth.sum(), 1.0)

        npix_f = p2.bg_width * p2.bg_height
        npix_s = float(np.mean(list(n_sig_per_frame.values()))) if n_sig_per_frame else 1.0
        prior_ratio = (npix_f - npix_s) / npix_s
        post_sig = prob_s / np.where(prob_s + prob_b * prior_ratio > 0,
                                     prob_s + prob_b * prior_ratio, 1.0)
        post_sig[prob_s == 0] = 0.0

        mask = post_sig > 0.1
        np.savez_compressed(raw_dir / "HSVmask.npz", mask=mask)

        progress.update(0.92, "plot", "Writing HSV plots…")
        self._write_hsvsig_plot(ctx, prob_s, mask=mask)
        self._write_hsvsig_plot(ctx, prob_b, mask=mask, filename="HSVbg.png",
                                title="HSV Background Counts — hollow square area ∝ n_bg")
        self._write_hsvsig_plot(ctx, post_sig, filename="HSVprob.png",
                                title="P(sig|HSV) — Bayesian posterior probability")
        self._write_hsvprob_hist(ctx, post_sig)

        result = {"ball_pixel_count": len(rows), "annotation_count": total,
                  "min_ball_radius": p2.min_ball_radius, "max_ball_radius": p2.max_ball_radius}
        (raw_dir / "result.json").write_text(json.dumps(result, indent=2))
        progress.update(1.0, "done", f"Sampled {len(rows)} ball pixels across {total} annotations")
        return result

    def _sample_bg_colors(self, ctx: PassContext, annotations: dict,
                          n_sig_per_frame: dict[str, int], rmin: float,
                          progress, start: float, end: float,
                          nbg_nsig_ratio: int = 100) -> np.ndarray:
        """For each annotated frame, randomly sample 20*N pixels outside 2*R exclusion circle."""
        rng = np.random.default_rng(seed=42)
        total = len(annotations)
        all_hsv: list[np.ndarray] = []

        cap = cv2.VideoCapture(str(ctx.video_path))
        try:
            for idx, (frame_key, ann) in enumerate(annotations.items()):
                progress.update(start + (end - start) * idx / max(total, 1),
                                 "bg", f"Background sampling frame {frame_key}…")
                n_sig = n_sig_per_frame.get(frame_key, 0)
                if n_sig == 0:
                    continue

                cx = ann.get("x", 0) if isinstance(ann, dict) else 0
                cy = ann.get("y", 0) if isinstance(ann, dict) else 0
                ann_radius = ann.get("radius", 0) if isinstance(ann, dict) else 0
                excl_radius = 2.0 * (ann_radius if ann_radius > 0 else rmin)

                frame_idx = int(frame_key) - 1  # browser → OpenCV
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ok, frame = cap.read()
                if not ok:
                    continue

                fh, fw = frame.shape[:2]
                ys, xs = np.mgrid[0:fh, 0:fw]
                dist2 = (xs - cx) ** 2 + (ys - cy) ** 2
                outside = dist2 > excl_radius ** 2
                cand_y = ys[outside]
                cand_x = xs[outside]

                n_want = nbg_nsig_ratio * n_sig
                n_avail = len(cand_y)
                if n_avail == 0:
                    continue
                chosen = rng.choice(n_avail, size=min(n_want, n_avail), replace=False)

                frame_hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                all_hsv.append(frame_hsv[cand_y[chosen], cand_x[chosen]])
        finally:
            cap.release()

        if all_hsv:
            bg_hsv = np.concatenate(all_hsv, axis=0).astype(np.float32)
            count_b, _ = np.histogramdd(bg_hsv, bins=[_H_EDGES, _S_EDGES, _V_EDGES])
        else:
            count_b = np.zeros((_H_BINS, _S_BINS, _V_BINS))
        return count_b

    def _write_hsvsig_plot(self, ctx: PassContext, count_s: np.ndarray,
                           filename: str = "HSVsig.png",
                           title: str = "HSV Signal Counts — hollow square area ∝ n_sig",
                           mask: np.ndarray | None = None) -> None:
        """2×4 grid of H×S subplots (one per V bin); hollow squares scaled by count.

        If mask is provided, contours at the bin-edge boundaries of masked regions are
        superimposed. Contours follow bin edges exactly (no interpolation smoothing).
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        h_mids = (np.arange(_H_BINS) + 0.5) * 180.0 / _H_BINS
        s_mids = (np.arange(_S_BINS) + 0.5) * 256.0 / _S_BINS
        h_step = 180.0 / _H_BINS   # cell width in data units
        s_step = 256.0 / _S_BINS   # cell height in data units

        max_count = float(count_s.max()) if count_s.max() > 0 else 1.0

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

            h_idx, s_idx = np.where(count_s[:, :, v_idx] > 0)
            if h_idx.size:
                counts = count_s[h_idx, s_idx, v_idx]
                # Square half-side in data units, scaled so max count fills the cell.
                # Area of square = (2*half)^2; we want area proportional to count.
                # At max count, half = h_step/2 (fills cell width).
                max_half_h = h_step / 2.0
                max_half_s = s_step / 2.0
                scale = np.sqrt(counts / max_count)
                half_h = scale * max_half_h
                half_s = scale * max_half_s

                cx = h_mids[h_idx]
                cy = s_mids[s_idx]
                dot_color = "white" if v_mid < 128 else "black"
                for x, y, dh, ds in zip(cx, cy, half_h, half_s):
                    rect = plt.Rectangle((x - dh, y - ds), 2 * dh, 2 * ds,
                                         linewidth=0.8, edgecolor=dot_color,
                                         facecolor="none")
                    ax.add_patch(rect)

            if mask is not None:
                sl = mask[:, :, v_idx]
                if sl.any():
                    from matplotlib.collections import LineCollection
                    h_edges = np.arange(_H_BINS + 1) * (180.0 / _H_BINS)
                    s_edges = np.arange(_S_BINS + 1) * (256.0 / _S_BINS)
                    padded = np.pad(sl, 1, constant_values=False)
                    # Vertical segments: boundary at h_edges[i] spanning s_edges[j..j+1]
                    v_bound = padded[:-1, 1:-1] != padded[1:, 1:-1]
                    vi, vj = np.where(v_bound)
                    v_segs = [[(h_edges[i], s_edges[j]), (h_edges[i], s_edges[j + 1])]
                               for i, j in zip(vi, vj)]
                    # Horizontal segments: boundary at s_edges[j] spanning h_edges[i..i+1]
                    h_bound = padded[1:-1, :-1] != padded[1:-1, 1:]
                    hi, hj = np.where(h_bound)
                    h_segs = [[(h_edges[i], s_edges[j]), (h_edges[i + 1], s_edges[j])]
                               for i, j in zip(hi, hj)]
                    contour_color = "white" if v_mid < 128 else "black"
                    ax.add_collection(LineCollection(v_segs + h_segs,
                                                     colors=contour_color, linewidths=0.8))

            ax.set_title(f"V bin {v_idx}  (V≈{v_mid:.0f})", fontsize=8)
            ax.set_xlabel("H", fontsize=7)
            ax.set_ylabel("S", fontsize=7)
            ax.tick_params(labelsize=6)

        fig.suptitle(title, fontsize=11)
        fig.tight_layout()
        fig.savefig(str(ctx.paths.pass_raw_dir / filename), dpi=120)
        plt.close(fig)

    def _write_hsvprob_hist(self, ctx: PassContext, post_sig: np.ndarray) -> None:
        """Histogram of P(sig|HSV) values for non-zero bins."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        vals = post_sig[post_sig > 0].ravel()
        fig, ax = plt.subplots(figsize=(8, 4))
        if vals.size:
            ax.hist(vals, bins=50, histtype="step", color="steelblue")
        ax.set_xlabel("P(sig|HSV)")
        ax.set_ylabel("bin count")
        ax.set_title("Distribution of Bayesian Posterior P(sig|HSV)  [non-zero bins only]")
        fig.tight_layout()
        fig.savefig(str(ctx.paths.pass_raw_dir / "HSVprob_hist.png"), dpi=120)
        plt.close(fig)

    def write_raw_outputs(self, ctx: PassContext, result: dict) -> list[dict]:
        raw_dir = ctx.paths.pass_raw_dir
        artifacts = []
        for name, typ in [
            ("result.json", "json"), ("ball_colors.csv", "csv"),
            ("HSVmask.npz", "npz"),
            ("HSVsig.png", "png"), ("HSVbg.png", "png"), ("HSVprob.png", "png"), ("HSVprob_hist.png", "png"),
        ]:
            p = raw_dir / name
            if p.exists():
                artifacts.append({"role": "raw", "type": typ, "path": str(p)})
        return artifacts

    def validate_corrections(self, payload: dict) -> dict:
        if "source_project_id" in payload:
            return {"source_project_id": str(payload["source_project_id"])}
        return {}

    def build_accepted_output(self, ctx: PassContext, raw_result: dict, corrections: dict | None) -> dict:
        import shutil
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)

        if corrections and "source_project_id" in corrections:
            source_accepted = (ctx.paths.project_root.parent
                               / corrections["source_project_id"]
                               / "passes" / "pass3" / "accepted")
            for name in ("HSVmask.npz", "result.json"):
                src = source_accepted / name
                if src.exists():
                    shutil.copy2(src, accepted_dir / name)
            return raw_result

        for name in ("result.json", "ball_colors.csv", "HSVmask.npz",
                     "HSVsig.png", "HSVbg.png", "HSVprob.png", "HSVprob_hist.png"):
            src = ctx.paths.pass_raw_dir / name
            if src.exists():
                shutil.copy2(src, accepted_dir / name)
        return raw_result
