"""Pass 5 — Segment Building: group ball detections into trajectory segments."""

from __future__ import annotations

import json
import math
from pathlib import Path

from pbva_core.types import Pass5AcceptedOutput, Pass5RawResult
from pbva_pipeline.base import PassContext


def _build_segments(
    detections: list[dict],
    max_gap_frames: int,
    max_pixels_per_frame: float,
) -> list[dict]:
    """Group flat detection list into trajectory segments.

    Two detections are linked when:
      - frame gap <= max_gap_frames
      - Euclidean distance <= max_pixels_per_frame * gap

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
                last = seg[-1]
                gap = frame - last["frame"]
                max_dist = max_pixels_per_frame * gap
                dist = math.hypot(det["cx"] - last["cx"], det["cy"] - last["cy"])
                if dist <= max_dist and dist < best_dist:
                    best_dist = dist
                    best_idx = i
            if best_idx >= 0:
                active[best_idx].append({"frame": frame, "cx": det["cx"], "cy": det["cy"], "radius": det["radius"]})
                used.add(best_idx)
            else:
                active.append([{"frame": frame, "cx": det["cx"], "cy": det["cy"], "radius": det["radius"]}])

    completed.extend(active)

    # Discard single-detection noise, sort by first frame, assign IDs.
    segments = []
    for i, dets in enumerate(sorted(
        (s for s in completed if len(s) >= 2),
        key=lambda s: s[0]["frame"],
    )):
        segments.append({
            "id": i,
            "first_frame": dets[0]["frame"],
            "last_frame": dets[-1]["frame"],
            "length": len(dets),
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

        max_gap_frames: int = 3
        max_pixels_per_frame: float = 50.0

        progress.update(0.05, "load", "Loading pass 4 detections…")
        det_path = ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json"
        raw = json.loads(det_path.read_text())
        detections: list[dict] = raw.get("detections", [])

        progress.update(0.10, "segment", f"Building segments from {len(detections)} detections…")
        segments = _build_segments(detections, max_gap_frames, max_pixels_per_frame)

        progress.update(0.90, "write", "Writing segments.json…")
        raw_dir = ctx.paths.pass_raw_dir
        output = {
            "segment_count": len(segments),
            "max_gap_frames": max_gap_frames,
            "max_pixels_per_frame": max_pixels_per_frame,
            "segments": segments,
        }
        (raw_dir / "segments.json").write_text(json.dumps(output, indent=2))

        progress.update(1.0, "done", f"Built {len(segments)} segments")
        return Pass5RawResult(
            segment_count=len(segments),
            max_gap_frames=max_gap_frames,
            max_pixels_per_frame=max_pixels_per_frame,
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
