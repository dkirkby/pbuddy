"""Pass 5 — Segment Building: identify in-flight ball trajectory tracks."""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np

from pbva_core.types import Pass5AcceptedOutput, Pass5RawResult
from pbva_pipeline.base import PassContext
from pbva_pipeline.pass5.tracking import Track, find_tracks_for_rally


# ---------------------------------------------------------------------------
# Algorithm parameters — fractional thresholds scale by hypot(bg_w, bg_h)
# ---------------------------------------------------------------------------

_INIT_SIZE             = 5
_MIN_PRUNED_SIZE       = 4
_MAX_FRAME_GAP         = 2
_MAX_TIME_GAP          = 0.25    # seconds

_MAX_INIT_SEP_FRAC     = 1.6342041321085299   # px/s per image-diagonal/s
_MAX_INIT_RES_FRAC     = 0.009078911845047388
_MAX_DV_FRAC           = 0.13618367767571082
_MAX_ISECT_RES_FRAC    = 0.004539455922523694
_ISECT_RES_SQRT_FRAC   = 0.002269727961261847
_MIN_ISECT_DV_FRAC     = 0.13618367767571082
_MIN_ISECT_REL_DV      = 0.7
_MIN_ISECT_ANG_DEG     = 15.0
_MIN_ISECT_SPD_FRAC    = 0.034045919418927704
_MIN_BBOX_PERIM_FRAC   = 0.02269727961261847
_MIN_CENTER_DELTA_FRAC = 0.02269727961261847
_MIN_LENGTH            = 0
_MIN_SPEED_FRAC        = 0.02269727961261847
_MAX_SPEED_FRAC        = 2.269727961261847


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_track(track: Track, track_id: int, fps: float) -> dict:
    all_dets = sorted(
        (
            {"frame": p["frame"], "cx": p["cx"], "cy": p["cy"],
             "radius": float(p.get("radius", 0.0))}
            for seg in track.segments
            for p in seg.points
        ),
        key=lambda d: d["frame"],
    )
    smooth_first_frame = round(float(track.smooth_t[0]) * fps)
    smooth = [
        [round(float(x), 2), round(float(y), 2)]
        for x, y in zip(track.smooth_x, track.smooth_y)
    ]
    return {
        "id":                 track_id,
        "rally_id":           track.rally_id,
        "first_frame":        all_dets[0]["frame"],
        "last_frame":         all_dets[-1]["frame"],
        "n_segments":         len(track.segments),
        "n_detections":       len(all_dets),
        "intersections":      [
            [round(float(x), 2), round(float(y), 2), round(float(t), 6)]
            for x, y, t in track.intersections
        ],
        "smooth_first_frame": smooth_first_frame,
        "smooth":             smooth,
        "detections":         all_dets,
    }


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def _plot_tracks(tracks: list[dict], bg_width: int, bg_height: int, out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(f"Pass 5 — {len(tracks)} tracks", fontsize=13)

    n_dets    = np.array([t["n_detections"] for t in tracks], dtype=float)
    n_segs    = np.array([t["n_segments"]   for t in tracks], dtype=float)
    spans     = np.array([t["last_frame"] - t["first_frame"] + 1 for t in tracks], dtype=float)
    rally_ids = np.array([t["rally_id"]     for t in tracks], dtype=float)

    ax = axes[0, 0]
    ax.scatter(spans, n_dets, s=20, alpha=0.7)
    ax.set_xlabel("frame span")
    ax.set_ylabel("detections")
    ax.set_title("span vs detections")

    _COLOR_CONFIGS = [
        (axes[0, 1], rally_ids, "rally id",   "colored by rally"),
        (axes[1, 0], n_dets,    "detections", "colored by detections"),
        (axes[1, 1], n_segs,    "segments",   "colored by segments"),
    ]
    for ax, values, cbar_label, title in _COLOR_CONFIGS:
        vmin, vmax = float(values.min()), float(values.max())
        norm = plt.Normalize(vmin=vmin, vmax=vmax if vmax > vmin else vmin + 1)
        colormap = cm.viridis
        for track, val in zip(tracks, values):
            xs = [pt[0] for pt in track["smooth"]]
            ys = [pt[1] for pt in track["smooth"]]
            ax.plot(xs, ys, color=colormap(norm(val)), linewidth=1.2, alpha=0.8)
        sm = cm.ScalarMappable(norm=norm, cmap=colormap)
        sm.set_array([])
        fig.colorbar(sm, ax=ax, label=cbar_label, fraction=0.046, pad=0.04)
        ax.set_xlim(0, bg_width)
        ax.set_ylim(bg_height, 0)
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_title(title)
        ax.set_xlabel("cx (px)")
        ax.set_ylabel("cy (px)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Pass5 class
# ---------------------------------------------------------------------------

class Pass5:
    name = "pass5"

    def validate_inputs(self, ctx: PassContext) -> None:
        required = [
            (ctx.paths.project_root / "passes" / "pass0" / "raw" / "result.json",
             "Pass 0 raw result.json not found — run Pass 0 first"),
            (ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json",
             "Pass 4 accepted detections.json not found — accept Pass 4 first"),
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json",
             "Pass 2 accepted rally.json not found — accept Pass 2 first"),
        ]
        for path, msg in required:
            if not path.exists():
                raise FileNotFoundError(msg)

    def run(self, ctx: PassContext, progress=None) -> Pass5RawResult:
        if progress is None:
            from pbva_pipeline.base import NullProgress
            progress = NullProgress()

        progress.update(0.02, "load", "Loading video metadata from pass 0…")
        p0_raw = json.loads(
            (ctx.paths.project_root / "passes" / "pass0" / "raw" / "result.json").read_text()
        )
        fps: float    = float(p0_raw["video_fps"])
        bg_width: int = int(p0_raw["bg_width"])
        bg_height: int = int(p0_raw["bg_height"])
        image_scale    = math.hypot(bg_width, bg_height)
        frame_center_x = 0.5 * bg_width

        max_init_sep     = _MAX_INIT_SEP_FRAC    * image_scale
        max_init_res     = _MAX_INIT_RES_FRAC    * image_scale
        max_dv           = _MAX_DV_FRAC          * image_scale
        max_isect_res    = _MAX_ISECT_RES_FRAC   * image_scale
        isect_res_sqrt   = _ISECT_RES_SQRT_FRAC  * image_scale
        min_isect_dv     = _MIN_ISECT_DV_FRAC    * image_scale
        min_isect_spd    = _MIN_ISECT_SPD_FRAC   * image_scale
        min_bbox_perim   = _MIN_BBOX_PERIM_FRAC  * image_scale
        min_center_delta = _MIN_CENTER_DELTA_FRAC * image_scale
        min_speed        = _MIN_SPEED_FRAC        * image_scale
        max_speed        = _MAX_SPEED_FRAC        * image_scale

        progress.update(0.04, "load", "Loading rally bounds from pass 2…")
        rally_data = json.loads(
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json").read_text()
        )
        rallies = rally_data.get("rally", [])

        progress.update(0.06, "load", "Loading pass 4 detections…")
        raw = json.loads(
            (ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json").read_text()
        )
        all_detections: list[dict] = raw.get("detections", [])

        all_tracks: list[Track] = []
        n_rallies  = len(rallies)
        base_frac  = 0.10
        rally_span = 0.82

        for ri, rally in enumerate(rallies):
            ri_frac     = base_frac + rally_span * ri / max(n_rallies, 1)
            # rally.json stores browser frame numbers; subtract 1 for OpenCV
            start_frame = rally["start_frame"] - 1
            stop_frame  = rally["stop_frame"]  - 1
            n_frames    = stop_frame - start_frame + 1
            label       = f"rally {ri + 1}/{n_rallies} ({n_frames} fr)"

            progress.update(ri_frac, "segment", f"{label}: filtering detections…")
            progress.check_cancelled()

            detections_by_frame: list[list[dict]] = [[] for _ in range(n_frames)]
            local_id = 0
            for d in all_detections:
                f = d["frame"]
                if start_frame <= f <= stop_frame:
                    detections_by_frame[f - start_frame].append(d | {"id": local_id})
                    local_id += 1

            n_rally_dets = sum(len(b) for b in detections_by_frame)
            progress.update(ri_frac, "segment",
                f"{label}: {n_rally_dets} detections — running tracker…")

            _, rally_tracks = find_tracks_for_rally(
                rally={"start_frame": start_frame, "stop_frame": stop_frame},
                rally_id=ri,
                detections_by_frame=detections_by_frame,
                init_size=_INIT_SIZE,
                min_pruned_size=_MIN_PRUNED_SIZE,
                max_frame_gap=_MAX_FRAME_GAP,
                max_init_sep=max_init_sep,
                max_init_residual=max_init_res,
                max_dv=max_dv,
                min_length=_MIN_LENGTH,
                min_speed=min_speed,
                max_speed=max_speed,
                FPS=fps,
                max_time_gap=_MAX_TIME_GAP,
                max_intersection_residual=max_isect_res,
                intersection_residual_per_sqrt_frame=isect_res_sqrt,
                min_intersection_dv=min_isect_dv,
                min_intersection_relative_dv=_MIN_ISECT_REL_DV,
                min_intersection_angle_deg=_MIN_ISECT_ANG_DEG,
                min_intersection_angle_speed=min_isect_spd,
                min_track_bbox_perimeter=min_bbox_perim,
                frame_center_x=frame_center_x,
                min_primary_centering_delta=min_center_delta,
            )
            all_tracks.extend(rally_tracks)
            progress.update(
                ri_frac + rally_span / max(n_rallies, 1),
                "segment",
                f"{label}: {len(rally_tracks)} tracks",
            )

        tracks_out = [_serialize_track(t, i, fps) for i, t in enumerate(all_tracks)]

        progress.update(0.93, "write", "Writing tracks.json…")
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        output = {
            "track_count": len(tracks_out),
            "fps":         fps,
            "bg_width":    bg_width,
            "bg_height":   bg_height,
            "tracks":      tracks_out,
        }
        (raw_dir / "tracks.json").write_text(json.dumps(output, indent=2))

        progress.update(0.97, "plot", "Plotting tracks…")
        if tracks_out:
            _plot_tracks(tracks_out, bg_width, bg_height, raw_dir / "tracks.png")

        progress.update(1.0, "done", f"Built {len(tracks_out)} tracks")
        return Pass5RawResult(track_count=len(tracks_out), fps=fps, image_scale=image_scale)

    def write_raw_outputs(self, ctx: PassContext, result: Pass5RawResult) -> list[dict]:
        artifacts = []
        for fname, atype in (("tracks.json", "json"), ("tracks.png", "image")):
            p = ctx.paths.pass_raw_dir / fname
            if p.exists():
                artifacts.append({"role": "raw", "type": atype, "path": str(p)})
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
        src = ctx.paths.pass_raw_dir / "tracks.json"
        kept: list[dict] = []
        if src.exists():
            raw_data = json.loads(src.read_text())
            deleted_ids: set[int] = (
                set(corrections.get("deleted_track_ids", [])) if corrections else set()
            )
            kept = [t for t in raw_data.get("tracks", []) if t["id"] not in deleted_ids]
            for i, track in enumerate(kept):
                track["id"] = i
            output = {**raw_data, "track_count": len(kept), "tracks": kept}
            (accepted_dir / "tracks.json").write_text(json.dumps(output, indent=2))
        accepted = Pass5AcceptedOutput(track_count=len(kept))
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
