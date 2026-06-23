"""Pass 5 — Segment Building: identify in-flight ball trajectory segments."""

from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, NamedTuple

import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np

from pbva_core.types import Pass2AcceptedOutput, Pass5AcceptedOutput, Pass5RawResult
from pbva_pipeline.base import PassContext


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class SegmentParams:
    fps: float
    R: float = 80.0                       # max displacement per adjacent frame (px)
    window: int = 5
    min_detections_per_window: int = 3
    max_missing_run: int = 2
    residual_tol_px: float = 3.0
    allow_one_outlier_per_window: bool = True
    outlier_factor: float = 2.0
    smooth_pos_tol_px: float = 6.0
    smooth_vel_tol_px_s: float = 300.0
    smooth_acc_tol_px_s2: float = 20000.0
    max_states_per_window: int | None = None
    max_paths_per_state: int = 20
    min_segment_frames: int = 5


# ---------------------------------------------------------------------------
# Window state
# ---------------------------------------------------------------------------

class WindowState(NamedTuple):
    start_frame: int
    assignment: tuple[int | None, ...]   # det_id or None for each of window frames
    coef: np.ndarray                     # shape (3, 2): [p0, v0, a0]
    residuals: tuple[float, ...]         # one per selected detection
    dropped_det_id: int | None           # outlier removed from this window, if any


# ---------------------------------------------------------------------------
# Quadratic fit helpers
# ---------------------------------------------------------------------------

def _fit_quad(
    frames: list[int],
    positions: np.ndarray,   # shape (m, 2)
    center_frame: int,
    fps: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit p(τ) = p0 + v0·τ + a0·(½τ²) to m selected detections.

    Returns coef (3,2) and per-detection residuals (m,).
    """
    taus = np.array([(f - center_frame) / fps for f in frames])
    A = np.column_stack([np.ones_like(taus), taus, 0.5 * taus ** 2])
    coef, *_ = np.linalg.lstsq(A, positions, rcond=None)
    residuals = np.linalg.norm(positions - A @ coef, axis=1)
    return coef, residuals


def _eval_fit(
    coef: np.ndarray,
    center_frame: int,
    query_frame: float,
    fps: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Evaluate quadratic fit at query_frame; returns (pos, vel, accel)."""
    tau = (query_frame - center_frame) / fps
    pos  = coef[0] + coef[1] * tau + coef[2] * 0.5 * tau ** 2
    vel  = coef[1] + coef[2] * tau
    acc  = coef[2].copy()
    return pos, vel, acc


# ---------------------------------------------------------------------------
# Window state building
# ---------------------------------------------------------------------------

def _enum_assignments(
    frame_choices: list[list[int | None]],
    min_det: int,
    max_miss_run: int,
) -> Iterator[list[int | None]]:
    """Yield valid assignment lists via recursive enumeration with early pruning."""
    W = len(frame_choices)
    buf: list[int | None] = []

    def _rec(fi: int, cur_run: int, n_det: int) -> Iterator[list[int | None]]:
        if fi == W:
            if n_det >= min_det:
                yield list(buf)
            return
        for choice in frame_choices[fi]:
            run = (cur_run + 1) if choice is None else 0
            if run > max_miss_run:
                continue
            nd = n_det + (0 if choice is None else 1)
            if nd + (W - fi - 1) < min_det:
                continue
            buf.append(choice)
            yield from _rec(fi + 1, run, nd)
            buf.pop()

    yield from _rec(0, 0, 0)


def _check_gate(frames: list[int], positions: np.ndarray, R: float) -> bool:
    for k in range(len(frames) - 1):
        if np.linalg.norm(positions[k + 1] - positions[k]) > R * (frames[k + 1] - frames[k]):
            return False
    return True


def _try_drop_outlier(
    assignment: list[int | None],
    frames: list[int],
    center_frame: int,
    id_to_pos: dict[int, np.ndarray],
    residuals: np.ndarray,
    params: SegmentParams,
) -> WindowState | None:
    """Remove the worst residual, refit, and return a WindowState if valid."""
    worst_sel = int(np.argmax(residuals))
    sel_count = -1
    worst_ai = -1
    for ai, x in enumerate(assignment):
        if x is not None:
            sel_count += 1
            if sel_count == worst_sel:
                worst_ai = ai
                break

    new_asgn = list(assignment)
    dropped = new_asgn[worst_ai]
    new_asgn[worst_ai] = None

    new_sel_frames = [frames[i] for i, x in enumerate(new_asgn) if x is not None]
    new_sel_ids    = [x for x in new_asgn if x is not None]
    if len(new_sel_ids) < params.min_detections_per_window:
        return None

    run = max_run = 0
    for x in new_asgn:
        run = (run + 1) if x is None else 0
        max_run = max(max_run, run)
    if max_run > params.max_missing_run:
        return None

    new_pos = np.array([id_to_pos[d] for d in new_sel_ids])
    if not _check_gate(new_sel_frames, new_pos, params.R):
        return None

    new_coef, new_res = _fit_quad(new_sel_frames, new_pos, center_frame, params.fps)
    if not np.all(new_res <= params.residual_tol_px):
        return None

    return WindowState(frames[0], tuple(new_asgn), new_coef, tuple(new_res.tolist()), dropped)


def _build_window_states(
    by_frame: dict[int, list[int]],
    id_to_pos: dict[int, np.ndarray],
    rally_start: int,
    rally_end: int,
    params: SegmentParams,
    progress_cb: Callable[[int, int, int], None] | None = None,
    progress_interval: int = 500,
    cancel_cb: Callable[[], None] | None = None,
) -> list[WindowState]:
    """Build all valid WindowState objects within one rally.

    progress_cb(frames_done, total_frames, states_so_far) called every progress_interval frames.
    cancel_cb() called every frame — should raise WorkerCancelled if cancellation is requested.
    """
    W = params.window
    states: list[WindowState] = []
    total_frames = max(rally_end - rally_start - W + 2, 0)

    for s in range(rally_start, rally_end - W + 2):
        if cancel_cb is not None:
            cancel_cb()
        if progress_cb is not None and (s - rally_start) % progress_interval == 0:
            progress_cb(s - rally_start, total_frames, len(states))
        frames = list(range(s, s + W))
        center = s + W // 2  # s + 2 for W=5

        frame_choices: list[list[int | None]] = [
            [None] + list(by_frame.get(f, []))
            for f in frames
        ]

        count = 0
        for assignment in _enum_assignments(frame_choices, params.min_detections_per_window, params.max_missing_run):
            if params.max_states_per_window is not None and count >= params.max_states_per_window:
                break

            sel_frames = [frames[i] for i, x in enumerate(assignment) if x is not None]
            sel_ids    = [x for x in assignment if x is not None]
            sel_pos    = np.array([id_to_pos[d] for d in sel_ids])

            if not _check_gate(sel_frames, sel_pos, params.R):
                continue

            coef, residuals = _fit_quad(sel_frames, sel_pos, center, params.fps)
            n_sel = len(sel_ids)

            if n_sel == 3 or np.all(residuals <= params.residual_tol_px):
                states.append(WindowState(s, tuple(assignment), coef, tuple(residuals.tolist()), None))
                count += 1
                continue

            if params.allow_one_outlier_per_window and n_sel > 3:
                ws = _try_drop_outlier(assignment, frames, center, id_to_pos, residuals, params)
                if ws is not None:
                    states.append(ws)
                    count += 1

    return states


# ---------------------------------------------------------------------------
# DAG construction
# ---------------------------------------------------------------------------

def _build_dag(states: list[WindowState], params: SegmentParams) -> dict[int, list[int]]:
    """Build directed edges between adjacent (consecutive start_frame) window states."""
    by_start: dict[int, list[int]] = {}
    for i, st in enumerate(states):
        by_start.setdefault(st.start_frame, []).append(i)

    edges: dict[int, list[int]] = {i: [] for i in range(len(states))}

    for i, a in enumerate(states):
        for j in by_start.get(a.start_frame + 1, []):
            b = states[j]
            if a.assignment[1:] != b.assignment[:-1]:
                continue
            f_cmp = a.start_frame + 2.5
            pa, va, aa = _eval_fit(a.coef, a.start_frame + 2, f_cmp, params.fps)
            pb, vb, ab = _eval_fit(b.coef, b.start_frame + 2, f_cmp, params.fps)
            if np.linalg.norm(pa - pb) > params.smooth_pos_tol_px:
                continue
            if np.linalg.norm(va - vb) > params.smooth_vel_tol_px_s:
                continue
            if np.linalg.norm(aa - ab) > params.smooth_acc_tol_px_s2:
                continue
            edges[i].append(j)

    return edges


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

def _path_to_assignment(
    path: tuple[int, ...],
    states: list[WindowState],
    W: int,
) -> list[tuple[int, int | None]]:
    frame_to_det: dict[int, int | None] = {}
    for si in path:
        st = states[si]
        for k, det_id in enumerate(st.assignment):
            frame_to_det[st.start_frame + k] = det_id
    first = states[path[0]].start_frame
    last  = states[path[-1]].start_frame + W - 1
    return [(f, frame_to_det.get(f)) for f in range(first, last + 1)]


def _extract_candidates(
    states: list[WindowState],
    edges: dict[int, list[int]],
    params: SegmentParams,
) -> list[dict]:
    """Enumerate all distinct candidate segments from DAG paths (DP with cap)."""
    n = len(states)
    paths_ending_at: list[list[tuple[int, ...]]] = [[(i,)] for i in range(n)]

    for i in sorted(range(n), key=lambda k: states[k].start_frame):
        for j in edges[i]:
            extended = [p + (j,) for p in paths_ending_at[i]]
            paths_ending_at[j].extend(extended)
            if len(paths_ending_at[j]) > params.max_paths_per_state:
                paths_ending_at[j].sort(key=len, reverse=True)
                paths_ending_at[j] = paths_ending_at[j][:params.max_paths_per_state]

    candidates: list[dict] = []
    seen: set[tuple[int, ...]] = set()

    for i in range(n):
        for path in paths_ending_at[i]:
            assignment = _path_to_assignment(path, states, params.window)

            # Trim to first/last detection
            fi = next((k for k, (_, d) in enumerate(assignment) if d is not None), None)
            if fi is None:
                continue
            li = len(assignment) - 1 - next(
                k for k, (_, d) in enumerate(reversed(assignment)) if d is not None
            )
            trimmed = assignment[fi: li + 1]

            if trimmed[-1][0] - trimmed[0][0] + 1 < params.min_segment_frames:
                continue

            det_ids = tuple(d for _, d in trimmed if d is not None)
            if det_ids in seen:
                continue
            seen.add(det_ids)

            candidates.append({"path": path, "assignment": trimmed, "det_ids": frozenset(det_ids)})

    return candidates


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_candidate(
    candidate: dict,
    states: list[WindowState],
    params: SegmentParams,
) -> tuple[float, dict]:
    path       = candidate["path"]
    assignment = candidate["assignment"]

    n_frames     = assignment[-1][0] - assignment[0][0] + 1
    n_detections = sum(1 for _, d in assignment if d is not None)
    n_missing    = n_frames - n_detections
    run = max_run = 0
    for _, d in assignment:
        run = (run + 1) if d is None else 0
        max_run = max(max_run, run)

    all_res  = [r for si in path for r in states[si].residuals]
    rms_vals = [
        math.sqrt(sum(r * r for r in states[si].residuals) / max(len(states[si].residuals), 1))
        for si in path
    ]
    mean_rms = float(np.mean(rms_vals))
    max_res  = float(max(all_res)) if all_res else 0.0

    vel_jumps: list[float] = []
    acc_jumps: list[float] = []
    for k in range(len(path) - 1):
        a, b = states[path[k]], states[path[k + 1]]
        f_cmp = a.start_frame + 2.5
        _, va, aa = _eval_fit(a.coef, a.start_frame + 2, f_cmp, params.fps)
        _, vb, ab = _eval_fit(b.coef, b.start_frame + 2, f_cmp, params.fps)
        vel_jumps.append(float(np.linalg.norm(va - vb)))
        acc_jumps.append(float(np.linalg.norm(aa - ab)))

    mean_vel = float(np.mean(vel_jumps)) if vel_jumps else 0.0
    mean_acc = float(np.mean(acc_jumps)) if acc_jumps else 0.0

    score = (
        10.0 * n_detections
        + 1.0 * n_frames
        - 2.0 * n_missing
        - 2.0 * mean_rms
        - 0.01 * mean_vel
        - 0.0001 * mean_acc
    )
    stats = {
        "n_frames": n_frames,
        "n_detections": n_detections,
        "n_missing": n_missing,
        "max_missing_run": max_run,
        "mean_local_rms_residual_px": round(mean_rms, 3),
        "max_local_residual_px": round(max_res, 3),
        "mean_smooth_vel_jump": round(mean_vel, 2),
        "mean_smooth_acc_jump": round(mean_acc, 2),
    }
    return score, stats


# ---------------------------------------------------------------------------
# Non-overlapping selection (MILP or greedy)
# ---------------------------------------------------------------------------

def _select_segments(candidates: list[dict]) -> list[int]:
    if not candidates:
        return []

    import scipy.sparse
    from scipy.optimize import Bounds, LinearConstraint, milp

    scores   = [c["score"] for c in candidates]
    det_sets = [c["det_ids"] for c in candidates]
    n        = len(candidates)
    all_dets = sorted({d for ds in det_sets for d in ds})
    det_idx  = {d: i for i, d in enumerate(all_dets)}
    m        = len(all_dets)
    rows: list[int] = []
    cols: list[int] = []
    for j, ds in enumerate(det_sets):
        for d in ds:
            rows.append(det_idx[d])
            cols.append(j)
    A = scipy.sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(m, n))
    result = milp(
        c=np.array([-s for s in scores]),
        constraints=LinearConstraint(A, lb=-np.inf, ub=np.ones(m)),
        integrality=np.ones(n),
        bounds=Bounds(lb=np.zeros(n), ub=np.ones(n)),
    )
    if not result.success:
        raise RuntimeError(f"MILP segment selection failed: {result.message}")
    return [j for j in range(n) if result.x[j] > 0.5]


# ---------------------------------------------------------------------------
# Per-frame kinematic estimates
# ---------------------------------------------------------------------------

def _compute_parameters(
    path: tuple[int, ...],
    assignment: list[tuple[int, int | None]],
    states: list[WindowState],
    params: SegmentParams,
) -> list[dict]:
    first_f = assignment[0][0]
    last_f  = assignment[-1][0]

    frame_states: dict[int, list[int]] = {}
    for si in path:
        st = states[si]
        for k in range(params.window):
            f = st.start_frame + k
            if first_f <= f <= last_f:
                frame_states.setdefault(f, []).append(si)

    parameters: list[dict] = []
    for f in range(first_f, last_f + 1):
        sis = frame_states.get(f)
        if not sis:
            continue
        ps, vs, accs = [], [], []
        for si in sis:
            st = states[si]
            p, v, a = _eval_fit(st.coef, st.start_frame + 2, f, params.fps)
            ps.append(p)
            vs.append(v)
            accs.append(a)
        parameters.append({
            "frame": f,
            "pos":   [round(float(x), 2) for x in np.mean(ps,   axis=0)],
            "vel":   [round(float(x), 2) for x in np.mean(vs,   axis=0)],
            "accel": [round(float(x), 2) for x in np.mean(accs, axis=0)],
        })
    return parameters


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _mean_speed_px_per_frame(dets: list[dict]) -> float:
    if len(dets) < 2:
        return 0.0
    steps = [
        math.hypot(dets[j + 1]["cx"] - dets[j]["cx"], dets[j + 1]["cy"] - dets[j]["cy"])
        / max(dets[j + 1]["frame"] - dets[j]["frame"], 1)
        for j in range(len(dets) - 1)
    ]
    return sum(steps) / len(steps)


# ---------------------------------------------------------------------------
# Segment plot (unchanged interface: uses id, length, mean_speed_px_per_frame, detections)
# ---------------------------------------------------------------------------

def _plot_corner(segments: list[dict], out_path: Path) -> None:
    """Corner plot of per-frame kinematic parameters across all selected segments."""
    rows = []
    for seg in segments:
        for p in seg.get("parameters", []):
            rows.append([
                p["pos"][0],   p["pos"][1],
                p["vel"][0],   p["vel"][1],
                p["accel"][0], p["accel"][1],
            ])
    if not rows:
        return

    data   = np.array(rows)
    labels = ["pos x (px)", "pos y (px)", "vel x (px/s)", "vel y (px/s)", "accel x (px/s²)", "accel y (px/s²)"]
    n      = len(labels)

    fig, axes = plt.subplots(n, n, figsize=(14, 14))
    fig.suptitle(f"Pass 5 kinematic corner plot  ({len(rows)} frame estimates, {len(segments)} segments)", fontsize=11)

    for i in range(n):
        for j in range(n):
            ax = axes[i, j]
            if i == j:
                ax.hist(data[:, i], bins=50, color="steelblue", alpha=0.75)
            elif i > j:
                ax.scatter(data[:, j], data[:, i], s=1, alpha=0.08, color="steelblue", rasterized=True)
            else:
                ax.set_visible(False)
                continue
            ax.set_xlabel(labels[j] if i == n - 1 else "")
            ax.set_ylabel(labels[i] if j == 0 else "")
            if i != n - 1:
                ax.set_xticklabels([])
            if j != 0:
                ax.set_yticklabels([])

    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def _plot_segments(segments: list[dict], out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle(f"Pass 5 — {len(segments)} segments", fontsize=13)

    lengths = np.array([s["length"] for s in segments], dtype=float)
    speeds  = np.array([s["mean_speed_px_per_frame"] for s in segments], dtype=float)
    scores  = np.array([s.get("score", 0.0) for s in segments], dtype=float)

    ax = axes[0, 0]
    ax.scatter(lengths, speeds, s=20, alpha=0.7)
    ax.set_xlabel("detections")
    ax.set_ylabel("mean speed (px/fr)")
    ax.set_title("detections vs mean speed")

    _COLOR_CONFIGS = [
        (axes[0, 1], lengths, "detections",       "colored by detections"),
        (axes[1, 0], speeds,  "mean speed (px/fr)", "colored by mean speed"),
        (axes[1, 1], scores,  "score",              "colored by score"),
    ]

    for ax, values, cbar_label, title in _COLOR_CONFIGS:
        vmin, vmax = values.min(), values.max()
        norm = plt.Normalize(vmin=vmin, vmax=vmax if vmax > vmin else vmin + 1)
        colormap = cm.viridis
        for seg, val in zip(segments, values):
            xs = [d["cx"] for d in seg["detections"]]
            ys = [d["cy"] for d in seg["detections"]]
            ax.plot(xs, ys, color=colormap(norm(val)), linewidth=1.2, alpha=0.8)
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


# ---------------------------------------------------------------------------
# Pass5 class
# ---------------------------------------------------------------------------

class Pass5:
    name = "pass5"

    def validate_inputs(self, ctx: PassContext) -> None:
        required = [
            (ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json",
             "Pass 4 accepted detections.json not found — accept Pass 4 first"),
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "result.json",
             "Pass 2 accepted result.json not found — accept Pass 2 first"),
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

        progress.update(0.02, "load", f"[{_ts()}] Loading fps and rally bounds from pass 2…")
        p2_result = Pass2AcceptedOutput.model_validate_json(
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "result.json").read_text()
        )
        fps = p2_result.fps

        rally_data = json.loads(
            (ctx.paths.project_root / "passes" / "pass2" / "accepted" / "rally.json").read_text()
        )
        # rally.json uses browser frame numbers; convert to OpenCV indices
        rally_intervals: list[tuple[int, int]] = [
            (r["start_frame"] - 1, r["stop_frame"] - 1)
            for r in rally_data.get("rally", [])
        ]

        progress.update(0.05, "load", f"[{_ts()}] Loading pass 4 detections…")
        raw = json.loads(
            (ctx.paths.project_root / "passes" / "pass4" / "accepted" / "detections.json").read_text()
        )
        all_detections: list[dict] = raw.get("detections", [])

        params = SegmentParams(fps=fps)

        id_to_det: dict[int, dict]        = dict(enumerate(all_detections))
        id_to_pos: dict[int, np.ndarray]  = {i: np.array([d["cx"], d["cy"]]) for i, d in enumerate(all_detections)}

        n_window_states = 0
        n_candidates    = 0
        all_segments: list[dict] = []

        base_frac = 0.1
        rally_frac_span = 0.80

        for ri, (rally_start, rally_end) in enumerate(rally_intervals):
            ri_frac = base_frac + rally_frac_span * ri / max(len(rally_intervals), 1)
            n_rally_frames = rally_end - rally_start + 1
            rally_label = f"rally {ri + 1}/{len(rally_intervals)} ({n_rally_frames} frames)"

            progress.update(ri_frac, "segment",
                f"[{_ts()}] {rally_label}: filtering detections…")
            progress.check_cancelled()

            rally_frame_set = set(range(rally_start, rally_end + 1))
            by_frame: dict[int, list[int]] = {}
            for i, d in enumerate(all_detections):
                if d["frame"] in rally_frame_set:
                    by_frame.setdefault(d["frame"], []).append(i)
            n_rally_dets = sum(len(v) for v in by_frame.values())
            progress.update(ri_frac, "segment",
                f"[{_ts()}] {rally_label}: {n_rally_dets} detections — building window states…")

            def _window_cb(frames_done: int, total: int, n_states: int) -> None:
                progress.check_cancelled()
                pct = frames_done / total if total else 0
                progress.update(
                    ri_frac + rally_frac_span / max(len(rally_intervals), 1) * pct * 0.6,
                    "segment",
                    f"[{_ts()}] {rally_label}: window states frame {frames_done}/{total} → {n_states} states",
                )

            states = _build_window_states(by_frame, id_to_pos, rally_start, rally_end, params,
                                          progress_cb=_window_cb,
                                          cancel_cb=progress.check_cancelled)
            n_window_states += len(states)
            progress.update(ri_frac + rally_frac_span / max(len(rally_intervals), 1) * 0.6,
                "segment",
                f"[{_ts()}] {rally_label}: {len(states)} window states — building DAG…")
            if not states:
                continue

            edges = _build_dag(states, params)
            n_edges = sum(len(v) for v in edges.values())
            progress.update(ri_frac + rally_frac_span / max(len(rally_intervals), 1) * 0.7,
                "segment",
                f"[{_ts()}] {rally_label}: DAG has {n_edges} edges — extracting candidates…")
            progress.check_cancelled()

            candidates = _extract_candidates(states, edges, params)
            progress.update(ri_frac + rally_frac_span / max(len(rally_intervals), 1) * 0.8,
                "segment",
                f"[{_ts()}] {rally_label}: {len(candidates)} candidates — scoring…")
            progress.check_cancelled()

            for c in candidates:
                c["score"], c["stats"] = _score_candidate(c, states, params)
            n_candidates += len(candidates)

            progress.update(ri_frac + rally_frac_span / max(len(rally_intervals), 1) * 0.9,
                "segment",
                f"[{_ts()}] {rally_label}: selecting non-overlapping segments (MILP)…")
            progress.check_cancelled()

            for ci in _select_segments(candidates):
                c          = candidates[ci]
                assignment = c["assignment"]
                dets       = [id_to_det[d] for _, d in assignment if d is not None]
                all_segments.append({
                    "score":      round(c["score"], 2),
                    "detections": dets,
                    "parameters": _compute_parameters(c["path"], assignment, states, params),
                    "stats":      c["stats"],
                })
            progress.update(ri_frac + rally_frac_span / max(len(rally_intervals), 1) * 1.0,
                "segment",
                f"[{_ts()}] {rally_label}: done — {len(all_segments)} segments so far")

        # Sort by first detection frame and assign final IDs
        all_segments.sort(key=lambda s: s["detections"][0]["frame"])
        segments: list[dict] = []
        for i, seg in enumerate(all_segments):
            dets = seg["detections"]
            segments.append({
                "id":                    i,
                "first_frame":           dets[0]["frame"],
                "last_frame":            dets[-1]["frame"],
                "length":                len(dets),
                "mean_speed_px_per_frame": round(_mean_speed_px_per_frame(dets), 2),
                "score":                 seg["score"],
                "detections":            dets,
                "parameters":            seg["parameters"],
                "stats":                 seg["stats"],
            })

        progress.update(0.92, "write", f"[{_ts()}] Writing segments.json…")
        raw_dir = ctx.paths.pass_raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        output = {
            "segment_count":       len(segments),
            "fps":                 fps,
            "R_px":                params.R,
            "residual_tol_px":     params.residual_tol_px,
            "smooth_pos_tol_px":   params.smooth_pos_tol_px,
            "smooth_vel_tol_px_s": params.smooth_vel_tol_px_s,
            "smooth_acc_tol_px_s2": params.smooth_acc_tol_px_s2,
            "min_segment_frames":  params.min_segment_frames,
            "segments":            segments,
            "summary": {
                "n_input_detections":   len(all_detections),
                "n_valid_window_states": n_window_states,
                "n_candidate_segments": n_candidates,
                "n_selected_segments":  len(segments),
                "n_assigned_detections": sum(s["stats"]["n_detections"] for s in segments),
            },
        }
        (raw_dir / "segments.json").write_text(json.dumps(output, indent=2))

        progress.update(0.96, "plot", f"[{_ts()}] Plotting segments…")
        if segments:
            _plot_segments(segments, raw_dir / "segments.png")
            _plot_corner(segments, raw_dir / "corner.png")

        progress.update(1.0, "done", f"[{_ts()}] Built {len(segments)} segments")
        return Pass5RawResult(segment_count=len(segments), fps=fps, R_px=params.R)

    def write_raw_outputs(self, ctx: PassContext, result: Pass5RawResult) -> list[dict]:
        artifacts = []
        for fname, atype in (("segments.json", "json"), ("segments.png", "image"), ("corner.png", "image")):
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
        src = ctx.paths.pass_raw_dir / "segments.json"
        kept: list[dict] = []
        if src.exists():
            raw_data = json.loads(src.read_text())
            deleted_ids: set[int] = set(corrections.get("deleted_segment_ids", [])) if corrections else set()
            kept = [s for s in raw_data.get("segments", []) if s["id"] not in deleted_ids]
            for i, seg in enumerate(kept):
                seg["id"] = i
            output = {**raw_data, "segment_count": len(kept), "segments": kept}
            (accepted_dir / "segments.json").write_text(json.dumps(output, indent=2))
        accepted = Pass5AcceptedOutput(segment_count=len(kept))
        (accepted_dir / "result.json").write_text(accepted.model_dump_json(indent=2))
        return accepted
