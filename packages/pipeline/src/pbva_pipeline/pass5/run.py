"""Pass 5 — Segment Building: group ball detections into trajectory segments."""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.cm as cm
import numpy as np

from pbva_core.types import Pass5AcceptedOutput, Pass5RawResult
from pbva_pipeline.base import PassContext


def _predict_position(seg: list[dict], frame: int) -> tuple[float, float] | None:
    """Linear velocity extrapolation from the last two detections.

    Returns (pred_cx, pred_cy) if the segment has at least 2 detections,
    otherwise None.
    """
    if len(seg) < 2:
        return None
    prev = seg[-2]
    last = seg[-1]
    dt = last["frame"] - prev["frame"]
    if dt == 0:
        return None
    vx = (last["cx"] - prev["cx"]) / dt
    vy = (last["cy"] - prev["cy"]) / dt
    gap = frame - last["frame"]
    return last["cx"] + vx * gap, last["cy"] + vy * gap


def _mean_speed(dets: list[dict]) -> float:
    if len(dets) < 2:
        return 0.0
    steps = [
        math.hypot(dets[j+1]["cx"] - dets[j]["cx"], dets[j+1]["cy"] - dets[j]["cy"])
        for j in range(len(dets) - 1)
    ]
    return sum(steps) / len(steps)


def _build_segments(
    detections: list[dict],
    max_gap_frames: int,
    large_gate_px: float,
    small_gate_px: float,
    min_segment_length: int,
    min_speed_px_per_frame: float,
) -> list[dict]:
    """Group flat detection list into trajectory segments.

    Two detections are linked when:
      - frame gap <= max_gap_frames
      - Euclidean distance from predicted position <= gate:
        - large_gate_px from last detection when segment has only 1 point
          (no velocity estimate available)
        - small_gate_px from linearly extrapolated position once 2+ points
          exist (velocity estimated from last two detections)

    Returns a list of segment dicts sorted by first_frame.  Segments with
    fewer than 2 detections (isolated noise) are discarded.
    """
    if not detections:
        return []

    # Group by frame.
    by_frame: dict[int, list[dict]] = {}
    for d in detections:
        by_frame.setdefault(d["frame"], []).append(d)

    active: list[list[dict]] = []   # each entry is the detections list of one active segment
    completed: list[list[dict]] = []

    for frame in sorted(by_frame):
        frame_dets = by_frame[frame]

        # Retire segments that have fallen too far behind.
        still_active = []
        for seg in active:
            if frame - seg[-1]["frame"] > max_gap_frames:
                completed.append(seg)
            else:
                still_active.append(seg)
        active = still_active

        # Match each detection to the nearest compatible active segment.
        # Prefer larger detections first so prominent candidates anchor segments.
        used: set[int] = set()
        for det in sorted(frame_dets, key=lambda d: d["radius"], reverse=True):
            best_idx, best_dist = -1, math.inf
            for i, seg in enumerate(active):
                if i in used:
                    continue
                pred = _predict_position(seg, frame)
                if pred is not None:
                    ref_cx, ref_cy = pred
                    gate = small_gate_px
                else:
                    last = seg[-1]
                    ref_cx, ref_cy = last["cx"], last["cy"]
                    gate = large_gate_px
                dist = math.hypot(det["cx"] - ref_cx, det["cy"] - ref_cy)
                if dist <= gate and dist < best_dist:
                    best_dist = dist
                    best_idx = i
            if best_idx >= 0:
                active[best_idx].append({"frame": frame, "cx": det["cx"], "cy": det["cy"], "radius": det["radius"]})
                used.add(best_idx)
            else:
                active.append([{"frame": frame, "cx": det["cx"], "cy": det["cy"], "radius": det["radius"]}])

    completed.extend(active)

    # Discard short or slow segments (noise), sort by first frame, assign IDs.
    segments = []
    for i, dets in enumerate(sorted(
        (s for s in completed if len(s) >= min_segment_length and _mean_speed(s) >= min_speed_px_per_frame),
        key=lambda s: s[0]["frame"],
    )):
        segments.append({
            "id": i,
            "first_frame": dets[0]["frame"],
            "last_frame": dets[-1]["frame"],
            "length": len(dets),
            "mean_speed_px_per_frame": round(_mean_speed(dets), 2),
            "detections": dets,
        })
    return segments


def _plot_segments(segments: list[dict], out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(f"Pass 5 — {len(segments)} segments", fontsize=13)

    lengths = np.array([s["length"] for s in segments], dtype=float)
    speeds = np.array([s["mean_speed_px_per_frame"] for s in segments], dtype=float)
    scores = lengths * speeds

    # Top-left: scatter length vs speed.
    ax = axes[0, 0]
    ax.scatter(lengths, speeds, s=20, alpha=0.7)
    ax.set_xlabel("length (frames)")
    ax.set_ylabel("mean speed (px/fr)")
    ax.set_title("length vs mean speed")

    # The three trajectory subplots share the same layout but differ in colormap value.
    _COLOR_CONFIGS = [
        (axes[0, 1], lengths, "length (frames)", "colored by length"),
        (axes[1, 0], speeds,  "mean speed (px/fr)", "colored by mean speed"),
        (axes[1, 1], scores,  "length × mean speed", "colored by length × speed"),
    ]

    for ax, values, cbar_label, title in _COLOR_CONFIGS:
        vmin, vmax = values.min(), values.max()
        norm = plt.Normalize(vmin=vmin, vmax=vmax if vmax > vmin else vmin + 1)
        colormap = cm.viridis
        for seg, val in zip(segments, values):
            xs = [d["cx"] for d in seg["detections"]]
            ys = [d["cy"] for d in seg["detections"]]
            color = colormap(norm(val))
            ax.plot(xs, ys, color=color, linewidth=1.2, alpha=0.8)
        sm = cm.ScalarMappable(norm=norm, cmap=colormap)
        sm.set_array([])
        fig.colorbar(sm, ax=ax, label=cbar_label, fraction=0.046, pad=0.04)
        ax.set_aspect("equal", adjustable="datalim")
        ax.invert_yaxis()
        ax.set_title(title)
        ax.set_xlabel("cx (px)")
        ax.set_ylabel("cy (px)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


class Pass5:
    name = "pass5"

    def validate_inputs(self, ctx: PassContext) -> None:
        det_path = ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json"
        if not det_path.exists():
            raise FileNotFoundError("Pass 4 accepted detections.json not found — accept Pass 4 first")

    def run(self, ctx: PassContext, progress=None) -> Pass5RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        max_gap_frames: int = 5
        large_gate_px: float = 150.0
        small_gate_px: float = 50.0
        min_segment_length: int = 5
        min_speed_px_per_frame: float = 5.0

        progress.update(0.05, "load", "Loading pass 4 detections…")
        det_path = ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json"
        raw = json.loads(det_path.read_text())
        detections: list[dict] = raw.get("detections", [])

        progress.update(0.10, "segment", f"Building segments from {len(detections)} detections…")
        segments = _build_segments(detections, max_gap_frames, large_gate_px, small_gate_px, min_segment_length, min_speed_px_per_frame)

        progress.update(0.90, "write", "Writing segments.json…")
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        output = {
            "segment_count": len(segments),
            "max_gap_frames": max_gap_frames,
            "large_gate_px": large_gate_px,
            "small_gate_px": small_gate_px,
            "min_segment_length": min_segment_length,
            "min_speed_px_per_frame": min_speed_px_per_frame,
            "segments": segments,
        }
        (raw_dir / "segments.json").write_text(json.dumps(output, indent=2))

        progress.update(0.95, "plot", "Plotting segments…")
        if segments:
            _plot_segments(segments, raw_dir / "segments.png")

        progress.update(1.0, "done", f"Built {len(segments)} segments")
        return Pass5RawResult(
            segment_count=len(segments),
            max_gap_frames=max_gap_frames,
            large_gate_px=large_gate_px,
            small_gate_px=small_gate_px,
            min_segment_length=min_segment_length,
        )

    def write_raw_outputs(self, ctx: PassContext, result: Pass5RawResult) -> list[dict]:
        artifacts = []
        json_path = ctx.paths.pass_raw_dir / "segments.json"
        if json_path.exists():
            artifacts.append({"role": "raw", "type": "json", "path": str(json_path)})
        png_path = ctx.paths.pass_raw_dir / "segments.png"
        if png_path.exists():
            artifacts.append({"role": "raw", "type": "image", "path": str(png_path)})
        return artifacts

    def validate_corrections(self, payload: dict) -> dict:
        return payload

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass5RawResult | dict,
        corrections: dict | None,
    ) -> Pass5AcceptedOutput:
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        src = ctx.paths.pass_raw_dir / "segments.json"
        if src.exists():
            raw_data = json.loads(src.read_text())
            deleted_ids: set[int] = set(corrections.get("deleted_segment_ids", [])) if corrections else set()
            kept = [s for s in raw_data.get("segments", []) if s["id"] not in deleted_ids]
            # Re-number IDs to be contiguous after deletions.
            for i, seg in enumerate(kept):
                seg["id"] = i
            output = {**raw_data, "segment_count": len(kept), "segments": kept}
            (accepted_dir / "segments.json").write_text(json.dumps(output, indent=2))
        count = len(kept) if src.exists() else 0
        accepted = Pass5AcceptedOutput(segment_count=count)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
