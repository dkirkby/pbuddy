"""Pass 5 — Segment Building: group ball detections into trajectory segments."""

from __future__ import annotations

import json
import math
from pathlib import Path

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


def _build_segments(
    detections: list[dict],
    max_gap_frames: int,
    large_gate_px: float,
    small_gate_px: float,
    min_segment_length: int,
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

    # Discard short segments (noise), sort by first frame, assign IDs.
    segments = []
    for i, dets in enumerate(sorted(
        (s for s in completed if len(s) >= min_segment_length),
        key=lambda s: s[0]["frame"],
    )):
        steps = [
            math.hypot(dets[j+1]["cx"] - dets[j]["cx"], dets[j+1]["cy"] - dets[j]["cy"])
            for j in range(len(dets) - 1)
        ]
        mean_speed = sum(steps) / len(steps) if steps else 0.0
        segments.append({
            "id": i,
            "first_frame": dets[0]["frame"],
            "last_frame": dets[-1]["frame"],
            "length": len(dets),
            "mean_speed_px_per_frame": round(mean_speed, 2),
            "detections": dets,
        })
    return segments


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

        progress.update(0.05, "load", "Loading pass 4 detections…")
        det_path = ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json"
        raw = json.loads(det_path.read_text())
        detections: list[dict] = raw.get("detections", [])

        progress.update(0.10, "segment", f"Building segments from {len(detections)} detections…")
        segments = _build_segments(detections, max_gap_frames, large_gate_px, small_gate_px, min_segment_length)

        progress.update(0.90, "write", "Writing segments.json…")
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        output = {
            "segment_count": len(segments),
            "max_gap_frames": max_gap_frames,
            "large_gate_px": large_gate_px,
            "small_gate_px": small_gate_px,
            "min_segment_length": min_segment_length,
            "segments": segments,
        }
        (raw_dir / "segments.json").write_text(json.dumps(output, indent=2))

        progress.update(1.0, "done", f"Built {len(segments)} segments")
        return Pass5RawResult(
            segment_count=len(segments),
            max_gap_frames=max_gap_frames,
            large_gate_px=large_gate_px,
            small_gate_px=small_gate_px,
            min_segment_length=min_segment_length,
        )

    def write_raw_outputs(self, ctx: PassContext, result: Pass5RawResult) -> list[dict]:
        path = ctx.paths.pass_raw_dir / "segments.json"
        return [{"role": "raw", "type": "json", "path": str(path)}] if path.exists() else []

    def validate_corrections(self, payload: dict) -> dict:
        return payload

    def build_accepted_output(
        self,
        ctx: PassContext,
        raw_result: Pass5RawResult | dict,
        corrections: dict | None,
    ) -> Pass5AcceptedOutput:
        import shutil
        accepted_dir = ctx.paths.pass_accepted_dir
        accepted_dir.mkdir(parents=True, exist_ok=True)
        src = ctx.paths.pass_raw_dir / "segments.json"
        if src.exists():
            shutil.copy2(src, accepted_dir / "segments.json")
        count = raw_result.segment_count if isinstance(raw_result, Pass5RawResult) else raw_result.get("segment_count", 0)
        accepted = Pass5AcceptedOutput(segment_count=count)
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
