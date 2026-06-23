"""Find pickleball ball trajectories from frame-by-frame image detections.

This module turns noisy 2D ball detections into longer physical tracks. The
input is a video metadata JSON file, accepted rally frame ranges, and per-frame
ball detections with image-pixel centroids. The output is a list of surviving
``SegmentBuilder`` objects and a list of ``Track`` objects grouped by rally.

The algorithm first builds short trajectory segments incrementally. Before a
segment is trusted, it collects candidate detections from several nearby frames
and prunes them to a viable initialization set using local quadratic fits. Once
pruned, a segment is extended only when the candidate detection is consistent
with a velocity extrapolated from recent parabolic fits.

After segment construction, duplicate detection ownership is resolved so a
single detection belongs to at most one surviving segment. Adjacent segments are
then combined into tracks when forward and backward endpoint extrapolations
intersect with a small residual. Intersections are retained as physical kinks
when the linked segments show a sufficiently large absolute, relative, or
angular velocity change; otherwise the link is treated as a smooth path through
an occluded or missing-detection gap. Tracks also store locally smoothed
frame-by-frame positions that preserve retained kink discontinuities.
"""


from itertools import combinations, product
import math
import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import minimize_scalar


class Parabola:
    """A 2D quadratic trajectory fitted through three image points in time."""

    coeffs: NDArray[np.float64]
    tmin: float
    tmax: float
    tmid: float

    def __init__(
            self,
            xy1: ArrayLike,
            xy2: ArrayLike,
            xy3: ArrayLike,
            t1: float,
            t2: float,
            t3: float,
            ) -> None:
        """Fit the unique quadratic x(t), y(t) passing through three timed points."""
        times = np.array([t1, t2, t3], dtype=float)
        points = np.array([xy1, xy2, xy3], dtype=float)

        if points.shape != (3, 2):
            raise ValueError("xy1, xy2, and xy3 must each contain exactly two values")
        if len(np.unique(times)) != 3:
            raise ValueError("t1, t2, and t3 must be distinct")

        self.tmin = float(np.min(times))
        self.tmax = float(np.max(times))
        self.tmid = 0.5 * (self.tmin + self.tmax)

        tau1, tau2, tau3 = times - self.tmid
        slope12 = (points[0] - points[1]) / (tau1 - tau2)
        slope13 = (points[0] - points[2]) / (tau1 - tau3)
        a = (slope12 - slope13) / (tau2 - tau3)
        b = slope12 - a * (tau1 + tau2)
        c = points[0] - a * tau1 * tau1 - b * tau1
        self.coeffs = np.array([a, b, c], dtype=float)

    def trange(self) -> tuple[float, float]:
        """Return the fitted time interval as ``(tmin, tmax)`` in seconds."""
        return self.tmin, self.tmax

    def evaluate(self, t: float) -> NDArray[np.float64]:
        """Evaluate the 2D position at time ``t`` and return ``np.array([x, y])``."""
        tau = float(t) - self.tmid
        return self.coeffs[0] * tau * tau + self.coeffs[1] * tau + self.coeffs[2]

    def velocity(self, t: float | None = None) -> NDArray[np.float64]:
        """Return the 2D velocity in pixels/sec at ``t``, defaulting to ``tmid``."""
        if t is None:
            t = self.tmid
        tau = float(t) - self.tmid
        return 2.0 * self.coeffs[0] * tau + self.coeffs[1]

    def acceleration(self) -> NDArray[np.float64]:
        """Return the constant 2D acceleration vector in pixels/sec**2."""
        return 2.0 * self.coeffs[0]

    def residual(self, x: float, y: float) -> float:
        """Return the closest 2D distance in pixels from ``(x, y)`` to the curve."""
        point = np.array([x, y], dtype=float)
        a = self.coeffs[0]
        b = self.coeffs[1]
        c = self.coeffs[2] - point

        # Minimize squared distance over the fitted time interval. Critical
        # points satisfy dot((a*tau**2 + b*tau + c), (2*a*tau + b)) == 0.
        deriv_coeffs = np.array([
            2.0 * np.dot(a, a),
            3.0 * np.dot(a, b),
            np.dot(b, b) + 2.0 * np.dot(a, c),
            np.dot(b, c),
        ])
        tau_min = self.tmin - self.tmid
        tau_max = self.tmax - self.tmid
        candidates = [tau_min, tau_max]

        nonzero = np.flatnonzero(np.abs(deriv_coeffs) > 1e-12)
        if len(nonzero):
            roots = np.roots(deriv_coeffs[nonzero[0]:])
            for root in roots:
                if abs(root.imag) < 1e-9:
                    tau = float(root.real)
                    if tau_min <= tau <= tau_max:
                        candidates.append(tau)

        distances = [math.hypot(*(a * tau * tau + b * tau + c)) for tau in candidates]
        return float(min(distances))


class SegmentBuilder:
    """Incrementally build one candidate in-flight ball trajectory segment.

    A SegmentBuilder starts from one detection and has two phases. Before pruning,
    ``self.candidates`` stores one list of candidate detections per frame. Each new
    frame contributes all detections that are close enough to any existing
    candidate, using ``max_init_sep / FPS`` pixels per frame and rejecting updates
    with frame gaps larger than ``max_frame_gap``.

    When ``init_size`` frame buckets have been accumulated, the builder prunes the
    candidate lists once. It searches for the largest time-ordered subset down to
    ``min_pruned_size`` buckets, choosing at most one detection per bucket. A subset
    is viable only if every parabola fit through every surviving triplet predicts
    every surviving point within ``max_init_residual`` pixels, and if consecutive
    surviving frames respect ``max_frame_gap``. If no viable subset exists, the
    builder is abandoned. If pruning succeeds, ``self.points`` becomes a plain list
    of accepted detection dicts and ``self.pruned`` remains the phase boundary; the
    segment is never pruned a second time.

    After pruning, each new frame is tested by velocity consistency. ``predict_v``
    linearly extrapolates recent saved local-parabola velocity estimates to the new
    frame. Each candidate detection is then paired with the previous two accepted
    detections to form a test parabola, and the candidate with the smallest
    velocity difference is accepted if that difference is below ``max_dv`` pixels
    per second.

    ``vfit[k]`` stores up to three 2D velocity estimates for accepted point ``k``:
    slot 0 from the fit through ``(k-2, k-1, k)``, slot 1 from ``(k-1, k, k+1)``,
    and slot 2 from ``(k, k+1, k+2)``. Unavailable estimates are filled with NaNs.

    Parameters are in frame numbers and image pixels except ``FPS`` and the two
    velocity-like thresholds. ``max_init_sep`` is supplied in pixels/sec and stored
    internally as pixels/frame; ``max_dv`` is pixels/sec; ``max_init_residual`` is
    pixels.
    """

    def __init__(self,
                 initial: dict,
                 rally: dict,
                 rally_id: int,
                 init_size: int,
                 min_pruned_size: int,
                 max_frame_gap: int,
                 max_init_sep: float, # pixels/sec will be divided by FPS
                 max_init_residual: float,
                 max_dv: float,
                 FPS: float) -> None:
        assert init_size > 3, "init_size too small"
        assert min_pruned_size > 3, "min_pruned_size too small for parabola residual check"
        assert min_pruned_size <= init_size, "min_pruned_size too large for init_size"
        self.candidates = [[initial]]  # type: list[list[dict]]
        self.points = []  # type: list[dict]
        self.rally = rally
        self.rally_id = int(rally_id)
        self.init_size = int(init_size)
        self.min_pruned_size = int(min_pruned_size)
        self.max_frame_gap = int(max_frame_gap)
        self.FPS = float(FPS)
        self.max_init_sep = float(max_init_sep / FPS)
        self.max_init_residual = float(max_init_residual)
        self.max_dv = float(max_dv)
        self.pruned = False
        self.abandoned = False
        self.vfit = [ ]

    def _point_xy(self, point: dict) -> NDArray[np.float64]:
        return np.array([point["cx"], point["cy"]], dtype=float)

    def _point_xy_tuple(self, point: dict) -> tuple[float, float]:
        return float(point["cx"]), float(point["cy"])

    def _point_separation(self, p1: dict, p2: dict) -> float:
        return math.hypot(float(p1["cx"]) - float(p2["cx"]), float(p1["cy"]) - float(p2["cy"]))

    def _point_time(self, point: dict) -> float:
        return point["frame"] / self.FPS

    def _update_fit(self, k: int) -> None:
        assert len(self.vfit) == k
        self.vfit.append(np.full((3, 2), np.nan))
        if k < 2:
            return

        pfit = Parabola(
            self._point_xy_tuple(self.points[k - 2]),
            self._point_xy_tuple(self.points[k - 1]),
            self._point_xy_tuple(self.points[k]),
            self._point_time(self.points[k - 2]),
            self._point_time(self.points[k - 1]),
            self._point_time(self.points[k]),
        )
        for point_index, fit_index in ((k, 0), (k - 1, 1), (k - 2, 2)):
            self.vfit[point_index][fit_index] = pfit.velocity(self._point_time(self.points[point_index]))

    def _prune_init_candidates(self) -> None:
        """Prune accumulated candidate buckets to a viable accepted point list."""
        assert len(self.candidates) == self.init_size, "not enough candidates to prune"

        candidate_lists = self.candidates

        def fit_triplet(points: tuple[dict, dict, dict]) -> Parabola:
            return Parabola(
                self._point_xy_tuple(points[0]),
                self._point_xy_tuple(points[1]),
                self._point_xy_tuple(points[2]),
                self._point_time(points[0]),
                self._point_time(points[1]),
                self._point_time(points[2]),
            )

        def score_candidate(points: tuple[dict, ...]) -> float | None:
            residuals = []
            for triplet in combinations(points, 3):
                parabola = fit_triplet(triplet)
                for point in points:
                    xy = parabola.evaluate(self._point_time(point))
                    residual = math.hypot(xy[0] - float(point["cx"]), xy[1] - float(point["cy"]))
                    if residual > self.max_init_residual:
                        return None
                    residuals.append(residual)
            return max(residuals, default=0.0)

        best_points = None
        best_score = np.inf
        frame_indices = range(len(candidate_lists))
        for size in range(self.init_size, self.min_pruned_size - 1, -1):
            for indices in combinations(frame_indices, size):
                frames = [candidate_lists[i][0]["frame"] for i in indices]
                if any(frames[i + 1] - frames[i] > self.max_frame_gap for i in range(len(frames) - 1)):
                    continue
                for points in product(*(candidate_lists[i] for i in indices)):
                    score = score_candidate(points)
                    if score is not None and score < best_score:
                        best_points = points
                        best_score = score
            if best_points is not None:
                break

        if best_points is None:
            self.pruned = False
            self.abandoned = True
            return

        self.points = list(best_points)
        self.candidates = []
        self.abandoned = False
        self.vfit = [ ]
        for k in range(len(self.points)):
            self._update_fit(k)
        self.pruned = True

    def get_points(self, t1: float | None = None, t2: float | None = None) -> tuple[NDArray[np.float64], NDArray[np.float64]] | None:
        """Return accepted detection x/y arrays in the time window, or None before pruning."""
        if not self.pruned:
            return None

        tmin = -np.inf if t1 is None else float(t1)
        tmax = np.inf if t2 is None else float(t2)
        points = [point for point in self.points if tmin <= self._point_time(point) <= tmax]
        x = np.array([point["cx"] for point in points], dtype=float)
        y = np.array([point["cy"] for point in points], dtype=float)
        return x, y

    def avg_speed(self) -> float:
        """Return endpoint displacement over duration in pixels/sec, or np.nan before pruning."""
        if not self.pruned:
            return np.nan

        t0 = self._point_time(self.points[0])
        t1 = self._point_time(self.points[-1])
        duration = t1 - t0
        if duration <= 0:
            return np.nan

        displacement = self._point_separation(self.points[-1], self.points[0])
        return float(displacement / duration)

    def predict_v(self, frame_index: int, interval_secs: float) -> NDArray[np.float64]:
        """Predict velocity at frame_index from recent saved velocity fits.

        Uses all finite vfit entries whose segment point time is in
        [tf - interval_secs, tf], where tf = frame_index / FPS. Returns
        np.full(2, np.nan) if there are too few saved velocity estimates.
        """
        if not self.pruned:
            return np.full(2, np.nan)

        tf = frame_index / self.FPS
        tmin = tf - float(interval_secs)
        times = []
        velocities = []
        for point, vfit in zip(self.points, self.vfit):
            t = self._point_time(point)
            if not (tmin <= t <= tf):
                continue
            for v in vfit:
                if np.all(np.isfinite(v)):
                    times.append(t)
                    velocities.append(v)

        if len(velocities) < 2 or len(np.unique(times)) < 2:
            return np.full(2, np.nan)

        dt = np.array(times, dtype=float) - tf
        v = np.array(velocities, dtype=float)
        design = np.column_stack([dt, np.ones(len(dt))])
        coeffs, *_ = np.linalg.lstsq(design, v, rcond=None)
        return coeffs[1]

    def build(self, points: list[dict]) -> list[dict]:
        """Build this segment using detections in the next frame.

        Returns the list of input points that remain available to start or extend
        other segments. Before pruning, all compatible points are copied into the
        candidate buckets. After pruning, at most one point is consumed from the
        input list when it passes the velocity-consistency gate.
        """
        if self.abandoned:
            return points

        frame = points[0]["frame"]
        last_frame = self.points[-1]["frame"] if self.pruned else self.candidates[-1][0]["frame"]
        dframe = frame - last_frame
        if dframe > self.max_frame_gap:
            return points

        if not self.pruned:
            assert len(self.candidates) < self.init_size, "unpruned segment should not exceed init_size"
            max_sep = self.max_init_sep * dframe
            new_points = [ ]
            for point in points:
                if any(self._point_separation(point, p) < max_sep for candidates in self.candidates for p in candidates):
                    new_points.append(point)
            if new_points:
                self.candidates.append(new_points)
                if len(self.candidates) == self.init_size:
                    self._prune_init_candidates()
        else:
            v_pred = self.predict_v(frame, 0.25)
            if not np.all(np.isfinite(v_pred)):
                return points

            max_dv = self.max_dv
            new_point = None
            for point in points:
                pxy = self._point_xy_tuple(point)
                test_fit = Parabola(
                    self._point_xy_tuple(self.points[-2]),
                    self._point_xy_tuple(self.points[-1]),
                    pxy,
                    self._point_time(self.points[-2]),
                    self._point_time(self.points[-1]),
                    frame / self.FPS,
                )
                test_v = test_fit.velocity(frame / self.FPS)
                test_dv = math.hypot(test_v[0] - v_pred[0], test_v[1] - v_pred[1])
                if test_dv < max_dv:
                    new_point = point
                    max_dv = test_dv
            if new_point:
                self.points.append(new_point)
                points.remove(new_point)
                self._update_fit(len(self.points) - 1)
                return points
        return points


def deduplicate_segments(segments: list[SegmentBuilder]) -> list[SegmentBuilder]:
    """Remove duplicate detection ownership across pruned segments.

    A detection id may appear in multiple pruned segment hypotheses because
    unpruned segments collect candidate detections without consuming them from
    the frame-level detection list. This function assigns each duplicated
    detection id to one owner segment, removes it from the other segments, then
    drops any segment whose remaining points are no longer viable.

    Ownership is deterministic: prefer the longer segment, then prefer the
    segment where the detection is farther from either endpoint, then prefer
    the earlier segment. A segment is dropped if deduplication leaves it with
    fewer than ``min_pruned_size`` points or creates a frame gap larger than
    ``max_frame_gap``. Fit caches are rebuilt for all surviving segments.
    """

    def detection_id(point: dict) -> int | None:
        return point.get("id")

    def build_usages(active: set[int], points_by_segment: dict[int, list[dict]]) -> dict[int, list[tuple[int, int]]]:
        usages = {}
        for segment_index in active:
            for point_index, point in enumerate(points_by_segment[segment_index]):
                det_id = detection_id(point)
                if det_id is None:
                    continue
                usages.setdefault(det_id, []).append((segment_index, point_index))
        return usages

    def duplicate_ids(usages: dict[int, list[tuple[int, int]]]) -> set[int]:
        return {det_id for det_id, uses in usages.items() if len({segment_index for segment_index, _ in uses}) > 1}

    def ownership_key(use: tuple[int, int], points_by_segment: dict[int, list[dict]]) -> tuple[int, int, int]:
        segment_index, point_index = use
        segment_size = len(points_by_segment[segment_index])
        endpoint_distance = min(point_index, segment_size - 1 - point_index)
        return (segment_size, endpoint_distance, -segment_index)

    def points_are_viable(segment: SegmentBuilder, points: list[dict]) -> bool:
        if len(points) < segment.min_pruned_size:
            return False
        return not any(
            points[k + 1]["frame"] - points[k]["frame"] > segment.max_frame_gap
            for k in range(len(points) - 1)
        )

    points_by_segment = {
        segment_index: list(segment.points)
        for segment_index, segment in enumerate(segments)
        if segment.pruned
    }
    active = set(points_by_segment)
    initial_duplicate_ids = duplicate_ids(build_usages(active, points_by_segment))
    proposed_points = {segment_index: points for segment_index, points in points_by_segment.items()}

    while active:
        usages = build_usages(active, points_by_segment)
        duplicated = duplicate_ids(usages)
        owners = {}
        for det_id in duplicated:
            owner_segment_index, _ = max(usages[det_id], key=lambda use: ownership_key(use, points_by_segment))
            owners[det_id] = owner_segment_index

        proposed_points = {}
        invalid_segments = set()
        for segment_index in active:
            points = [
                point for point in points_by_segment[segment_index]
                if owners.get(detection_id(point), segment_index) == segment_index
            ]
            proposed_points[segment_index] = points
            if not points_are_viable(segments[segment_index], points):
                invalid_segments.add(segment_index)

        if not invalid_segments:
            break
        active -= invalid_segments

    surviving_segments = []
    removed_points = 0
    dropped_segments = 0
    for segment_index, segment in enumerate(segments):
        original_points = points_by_segment.get(segment_index, [])
        if segment_index not in active:
            if segment.pruned:
                removed_points += len(original_points)
                dropped_segments += 1
            segment.points = []
            segment.candidates = []
            segment.vfit = []
            segment.pruned = False
            segment.abandoned = True
            continue

        segment.points = proposed_points[segment_index]
        removed_points += len(original_points) - len(segment.points)
        segment.candidates = []
        segment.vfit = []
        segment.abandoned = False
        segment.pruned = True
        for k in range(len(segment.points)):
            segment._update_fit(k)
        surviving_segments.append(segment)

    if initial_duplicate_ids:
        print(
            f"Deduplicated {len(initial_duplicate_ids)} detection ids: "
            f"removed {removed_points} point uses and dropped {dropped_segments} segments"
        )
    return surviving_segments


class Track:
    """A physical trajectory assembled from one or more SegmentBuilder pieces.

    Attributes
    ----------
    rally_id
        Integer rally index shared by all segments in this track.
    segments
        Ordered list of ``SegmentBuilder`` pieces linked into this physical
        trajectory.
    intersections
        Ordered ``(x, y, t)`` tuples for linked-segment intersections that are
        retained as genuine velocity discontinuities. Coordinates are image
        pixels and times are seconds; intersection times are continuous and are
        not rounded to frame centers.
    smooth_t
        Float array of frame-centered sample times, in seconds, spanning this
        track from its first detection frame through its last detection frame.
    smooth_x
        Float array of locally smoothed x coordinates, in image pixels, aligned
        one-to-one with ``smooth_t``.
    smooth_y
        Float array of locally smoothed y coordinates, in image pixels, aligned
        one-to-one with ``smooth_t``.
    """

    rally_id: int
    segments: list[SegmentBuilder]
    intersections: list[tuple[float, float, float]]
    smooth_t: NDArray[np.float64]
    smooth_x: NDArray[np.float64]
    smooth_y: NDArray[np.float64]

    def __init__(
            self,
            segments: list[SegmentBuilder],
            intersections: list[tuple[float, float, float]] | None = None,
            ) -> None:
        """Store combined segments and their velocity-discontinuous intersections.

        Parameters
        ----------
        segments
            Non-empty ordered list of ``SegmentBuilder`` pieces to combine into
            this track. All segments must have the same ``rally_id``.
        intersections
            Optional ordered ``(x, y, t)`` tuples for linked-segment intersections
            to retain as velocity discontinuities. Coordinates are image pixels
            and times are seconds.
        """
        self.segments = list(segments)
        assert self.segments, "Track requires at least one segment"
        self.rally_id = self.segments[0].rally_id
        assert all(segment.rally_id == self.rally_id for segment in self.segments), "Track segments must share rally_id"
        self.intersections = [] if intersections is None else list(intersections)
        self.update_smooth()

    def get_points(self, t1: float | None = None, t2: float | None = None) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Return smoothed frame samples and intersections in increasing time order.

        Smoothed samples are included for every video frame spanned by the track.
        Velocity-discontinuous intersection points are also included when their
        continuous intersection times fall within the requested time window.
        The optional ``t1`` and ``t2`` bounds are inclusive and measured in
        seconds. Empty windows return empty float arrays.

        Parameters
        ----------
        t1
            Optional inclusive lower time bound in seconds. ``None`` means no
            lower bound.
        t2
            Optional inclusive upper time bound in seconds. ``None`` means no
            upper bound.

        Returns
        -------
        tuple[NDArray[np.float64], NDArray[np.float64]]
            ``(x, y)`` arrays in image pixels, sorted by increasing sample time.
        """
        tmin = -np.inf if t1 is None else float(t1)
        tmax = np.inf if t2 is None else float(t2)

        samples = [
            (float(t), float(x), float(y))
            for t, x, y in zip(self.smooth_t, self.smooth_x, self.smooth_y)
            if tmin <= t <= tmax
        ]
        samples.extend(
            (float(t), float(x), float(y))
            for x, y, t in self.intersections
            if tmin <= t <= tmax
        )
        samples.sort(key=lambda sample: sample[0])

        x = np.array([x for t, x, y in samples], dtype=float)
        y = np.array([y for t, x, y in samples], dtype=float)
        return x, y

    def _smooth_piece(
            self,
            samples: list[tuple[float, NDArray[np.float64]]],
            target_times: NDArray[np.float64],
            ) -> list[tuple[float, NDArray[np.float64]]]:
        """Return locally smoothed ``(t, xy)`` samples for one smooth piece.

        Parameters
        ----------
        samples
            Detection samples for one smooth, kink-free piece as ``(t, xy)``
            tuples. Times are seconds and ``xy`` values are 2D image-pixel arrays.
        target_times
            Frame-centered times, in seconds, where smoothed positions should be
            evaluated.

        Returns
        -------
        list[tuple[float, NDArray[np.float64]]]
            Smoothed ``(t, xy)`` samples evaluated at ``target_times``.
        """
        if len(samples) == 0:
            return []

        times = np.array([t for t, xy in samples], dtype=float)
        xy = np.array([xy for t, xy in samples], dtype=float)
        if len(samples) == 1:
            return [(float(t), xy[0].copy()) for t in target_times]
        if len(samples) == 2:
            smooth_x = np.interp(target_times, times, xy[:, 0])
            smooth_y = np.interp(target_times, times, xy[:, 1])
            return [(float(t), np.array([x, y], dtype=float)) for t, x, y in zip(target_times, smooth_x, smooth_y)]

        def evaluate_stencil(indices: tuple[int, int, int], t: float) -> NDArray[np.float64] | None:
            if len(np.unique(times[list(indices)])) != 3:
                return None
            parabola = Parabola(
                xy[indices[0]], xy[indices[1]], xy[indices[2]],
                times[indices[0]], times[indices[1]], times[indices[2]],
            )
            return parabola.evaluate(t)

        smoothed_samples = []
        for t in target_times:
            estimates = []
            exact_indices = np.flatnonzero(np.isclose(times, t, rtol=0.0, atol=1e-10))
            if len(exact_indices):
                k = int(exact_indices[0])
                for indices in ((k - 1, k + 1, k + 2), (k - 2, k - 1, k + 1)):
                    if all(0 <= index < len(samples) for index in indices):
                        estimate = evaluate_stencil(indices, float(t))
                        if estimate is not None:
                            estimates.append(estimate)

            if not estimates:
                insertion = int(np.searchsorted(times, t))
                starts = sorted(set(max(0, min(len(samples) - 3, start)) for start in (insertion - 2, insertion - 1, insertion)))
                for start in starts:
                    estimate = evaluate_stencil((start, start + 1, start + 2), float(t))
                    if estimate is not None:
                        estimates.append(estimate)

            if estimates:
                smoothed_samples.append((float(t), np.mean(estimates, axis=0)))
            else:
                # All parabola stencils failed (e.g. duplicate sample times from
                # overlapping segments); fall back to nearest-neighbour position.
                idx = int(np.argmin(np.abs(times - float(t))))
                smoothed_samples.append((float(t), xy[idx].copy()))

        return smoothed_samples

    def update_smooth(self) -> None:
        """Locally smooth track positions at every spanned frame without crossing kinks.

        Detection frames use a local leave-one-out quadratic prediction from
        neighboring detections in the same smooth piece. Missing frames use
        nearby three-point quadratic stencils. Retained velocity-discontinuous
        intersections split smooth pieces so the local stencil does not cross a
        physical kink.

        Parameters
        ----------
        None

        Returns
        -------
        None
            Updates ``smooth_t``, ``smooth_x``, and ``smooth_y`` in place.
        """
        if not self.segments:
            self.smooth_t = np.array([], dtype=float)
            self.smooth_x = np.array([], dtype=float)
            self.smooth_y = np.array([], dtype=float)
            return

        boundaries = np.array([t for x, y, t in self.intersections], dtype=float)
        smooth_pieces = []
        current_piece = []
        next_boundary = 0
        for k, segment in enumerate(self.segments):
            current_piece.append(segment)
            if k == len(self.segments) - 1:
                continue
            t0 = segment._point_time(segment.points[-1])
            t1 = self.segments[k + 1]._point_time(self.segments[k + 1].points[0])
            if next_boundary < len(boundaries) and t0 <= boundaries[next_boundary] <= t1:
                smooth_pieces.append(current_piece)
                current_piece = []
                next_boundary += 1
        smooth_pieces.append(current_piece)

        first_frame = min(point["frame"] for segment in self.segments for point in segment.points)
        last_frame = max(point["frame"] for segment in self.segments for point in segment.points)
        all_target_times = np.arange(first_frame, last_frame + 1, dtype=float) / self.segments[0].FPS
        piece_target_times = [[] for piece in smooth_pieces]
        for t in all_target_times:
            piece_index = int(np.searchsorted(boundaries, t, side="right"))
            piece_index = min(piece_index, len(smooth_pieces) - 1)
            piece_target_times[piece_index].append(t)

        smoothed_samples = []
        for piece, target_times in zip(smooth_pieces, piece_target_times):
            if not target_times:
                continue
            samples = [
                (segment._point_time(point), segment._point_xy(point))
                for segment in piece
                for point in segment.points
            ]
            samples.sort(key=lambda sample: sample[0])
            smoothed_samples.extend(self._smooth_piece(samples, np.array(target_times, dtype=float)))
        smoothed_samples.sort(key=lambda sample: sample[0])

        self.smooth_t = np.array([t for t, xy in smoothed_samples], dtype=float)
        smooth_xy = np.array([xy for t, xy in smoothed_samples], dtype=float)
        self.smooth_x = smooth_xy[:, 0]
        self.smooth_y = smooth_xy[:, 1]


def combine_segments(
        segments: list[SegmentBuilder],
        max_time_gap: float,
        max_intersection_residual: float = 10.0,
        intersection_residual_per_sqrt_frame: float = 5.0,
        min_intersection_dv: float = 300.0,
        min_intersection_relative_dv: float = 0.7,
        min_intersection_angle_deg: float = 15.0,
        min_intersection_angle_speed: float = 75.0,
        max_overlap_frames: int = 2,
        min_track_bbox_perimeter: float = 50.0,
        ) -> list[Track]:
    """Combine segment pieces whose endpoint extrapolations intersect.

    Candidate links are considered only from an earlier segment A to a later
    segment B when the time from A's final detection to B's first detection is
    no larger than ``max_time_gap`` seconds and the segments overlap by no more
    than ``max_overlap_frames`` frames. A is extrapolated with a parabola through
    its final three points and B with a parabola through its first three points.
    The accepted intersection is the continuous time in the temporal gap or
    allowed overlap that minimizes the extrapolated separation. The residual
    threshold grows as ``max_intersection_residual +
    intersection_residual_per_sqrt_frame * sqrt(frame_gap)`` pixels.
    Accepted links are stored as ``Track.intersections`` only when the
    extrapolated velocity vectors differ by at least ``min_intersection_dv``
    pixels/sec or by a fractional vector change of at least
    ``min_intersection_relative_dv`` relative to the larger endpoint speed,
    or by a velocity direction change of at least
    ``min_intersection_angle_deg`` degrees when both endpoint speeds are at
    least ``min_intersection_angle_speed`` pixels/sec,
    indicating a likely physical discontinuity rather than a smooth trajectory
    with missing detections.

    Links are selected greedily by smallest intersection residual, then shortest
    time gap, then longer combined segment length. Each segment can have at most
    one predecessor and one successor, so accepted links form ordered tracks.
    Final tracks whose smoothed-position bounding-box perimeter is less than
    ``min_track_bbox_perimeter`` pixels are discarded.
    """

    def segment_start_time(segment: SegmentBuilder) -> float:
        return segment._point_time(segment.points[0])

    def segment_end_time(segment: SegmentBuilder) -> float:
        return segment._point_time(segment.points[-1])

    def endpoint_parabola(segment: SegmentBuilder, at_start: bool) -> Parabola | None:
        if len(segment.points) < 3:
            return None
        points = segment.points[:3] if at_start else segment.points[-3:]
        return Parabola(
            segment._point_xy_tuple(points[0]),
            segment._point_xy_tuple(points[1]),
            segment._point_xy_tuple(points[2]),
            segment._point_time(points[0]),
            segment._point_time(points[1]),
            segment._point_time(points[2]),
        )

    def track_bbox_perimeter(track: Track) -> float:
        if len(track.smooth_x) == 0:
            return 0.0
        width = np.max(track.smooth_x) - np.min(track.smooth_x)
        height = np.max(track.smooth_y) - np.min(track.smooth_y)
        return float(2.0 * (width + height))

    def intersection_candidate(
            segment_a: SegmentBuilder,
            segment_b: SegmentBuilder,
            ) -> tuple[float, float, float, float, float, float, float, float] | None:
        t0 = segment_end_time(segment_a)
        t1 = segment_start_time(segment_b)
        gap = t1 - t0
        frame_gap = segment_b.points[0]["frame"] - segment_a.points[-1]["frame"]
        if frame_gap < -max_overlap_frames or gap > max_time_gap:
            return None

        parabola_a = endpoint_parabola(segment_a, at_start=False)
        parabola_b = endpoint_parabola(segment_b, at_start=True)
        if parabola_a is None or parabola_b is None:
            return None

        def separation(t: float) -> float:
            xy_a = parabola_a.evaluate(t)
            xy_b = parabola_b.evaluate(t)
            return math.hypot(xy_a[0] - xy_b[0], xy_a[1] - xy_b[1])

        if gap == 0:
            t_intersect = t0
            residual = separation(t_intersect)
        else:
            result = minimize_scalar(separation, bounds=(min(t0, t1), max(t0, t1)), method="bounded")
            if not result.success:
                return None
            t_intersect = float(result.x)
            residual = float(result.fun)

        allowed_residual = max_intersection_residual + intersection_residual_per_sqrt_frame * np.sqrt(max(frame_gap, 0))
        if residual > allowed_residual:
            return None

        xy = 0.5 * (parabola_a.evaluate(t_intersect) + parabola_b.evaluate(t_intersect))
        velocity_a = parabola_a.velocity(t_intersect)
        velocity_b = parabola_b.velocity(t_intersect)
        velocity_delta = math.hypot(velocity_a[0] - velocity_b[0], velocity_a[1] - velocity_b[1])
        speed_a = math.hypot(velocity_a[0], velocity_a[1])
        speed_b = math.hypot(velocity_b[0], velocity_b[1])
        velocity_scale = max(speed_a, speed_b)
        relative_velocity_delta = velocity_delta / velocity_scale if velocity_scale > 0 else 0.0
        if speed_a > 0 and speed_b > 0:
            cosine = (velocity_a[0] * velocity_b[0] + velocity_a[1] * velocity_b[1]) / (speed_a * speed_b)
            velocity_angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        else:
            velocity_angle = 0.0
        min_velocity_speed = min(speed_a, speed_b)
        return (
            float(xy[0]), float(xy[1]), t_intersect, residual,
            float(velocity_delta), float(relative_velocity_delta),
            float(velocity_angle), float(min_velocity_speed),
        )

    pruned_segments = [segment for segment in segments if segment.pruned and segment.points]
    ordered_segments = sorted(pruned_segments, key=lambda segment: (segment_start_time(segment), segment_end_time(segment)))

    candidate_links = []
    for i, segment_a in enumerate(ordered_segments):
        for j in range(i + 1, len(ordered_segments)):
            segment_b = ordered_segments[j]
            gap = segment_start_time(segment_b) - segment_end_time(segment_a)
            if gap > max_time_gap:
                break
            candidate = intersection_candidate(segment_a, segment_b)
            if candidate is None:
                continue
            x, y, t_intersect, residual, velocity_delta, relative_velocity_delta, velocity_angle, min_velocity_speed = candidate
            combined_length = len(segment_a.points) + len(segment_b.points)
            is_discontinuous = (
                velocity_delta >= min_intersection_dv
                or relative_velocity_delta >= min_intersection_relative_dv
                or (velocity_angle >= min_intersection_angle_deg and min_velocity_speed >= min_intersection_angle_speed)
            )
            candidate_links.append((residual, gap, -combined_length, i, j, (x, y, t_intersect), is_discontinuous))

    successor = {}
    predecessor = {}
    intersections = {}
    discontinuous = {}
    for residual, gap, neg_length, i, j, intersection, is_discontinuous in sorted(candidate_links):
        if i in successor or j in predecessor:
            continue
        successor[i] = j
        predecessor[j] = i
        intersections[(i, j)] = intersection
        discontinuous[(i, j)] = is_discontinuous

    tracks = []
    used = set()
    for i, segment in enumerate(ordered_segments):
        if i in used or i in predecessor:
            continue
        track_segments = [segment]
        track_intersections = []
        used.add(i)
        tail = i
        while tail in successor:
            next_index = successor[tail]
            if discontinuous[(tail, next_index)]:
                track_intersections.append(intersections[(tail, next_index)])
            track_segments.append(ordered_segments[next_index])
            used.add(next_index)
            tail = next_index
        tracks.append(Track(track_segments, track_intersections))

    for i, segment in enumerate(ordered_segments):
        if i not in used:
            tracks.append(Track([segment]))

    return [track for track in tracks if track_bbox_perimeter(track) >= min_track_bbox_perimeter]


def get_primary_tracks(
        tracks: list[Track],
        frame_center_x: float = 960.0,
        min_centering_delta: float = 50.0,
        min_overlap_frames: int = 1,
        ) -> list[Track]:
    """Return tracks that are not clearly secondary during time overlaps.

    Tracks are assumed to come from one rally. When two tracks have smoothed
    points in the same frame, the track with the smaller mean horizontal
    distance to ``frame_center_x`` over the overlapping frames is preferred.
    The less-centered track is removed only when the difference in mean distance
    is at least ``min_centering_delta`` pixels; ambiguous overlaps keep both
    tracks for later review.
    """
    if len(tracks) <= 1:
        return list(tracks)

    rally_ids = {track.rally_id for track in tracks}
    if len(rally_ids) != 1:
        raise ValueError("get_primary_tracks expects tracks from a single rally")

    def x_by_frame(track: Track) -> dict[int, float]:
        fps = track.segments[0].FPS
        frames = np.rint(track.smooth_t * fps).astype(int)
        values = {}
        for frame in np.unique(frames):
            values[int(frame)] = float(np.mean(track.smooth_x[frames == frame]))
        return values

    track_x_by_frame = [x_by_frame(track) for track in tracks]
    keep = np.ones(len(tracks), dtype=bool)
    for i, j in combinations(range(len(tracks)), 2):
        overlap = sorted(set(track_x_by_frame[i]) & set(track_x_by_frame[j]))
        if len(overlap) < min_overlap_frames:
            continue

        center_distance_i = np.mean([abs(track_x_by_frame[i][frame] - frame_center_x) for frame in overlap])
        center_distance_j = np.mean([abs(track_x_by_frame[j][frame] - frame_center_x) for frame in overlap])
        if abs(center_distance_i - center_distance_j) < min_centering_delta:
            continue
        if center_distance_i < center_distance_j:
            keep[j] = False
        else:
            keep[i] = False

    return [track for track, should_keep in zip(tracks, keep) if should_keep]


def find_tracks_for_rally(
        rally: dict,
        rally_id: int,
        detections_by_frame: list[list[dict]],
        init_size: int,
        min_pruned_size: int,
        max_frame_gap: int,
        max_init_sep: float,
        max_init_residual: float,
        max_dv: float,
        min_length: int,
        min_speed: float,   # pixels/sec
        max_speed: float, # pixels/sec
        FPS: float,
        max_time_gap: float,
        max_intersection_residual: float,
        intersection_residual_per_sqrt_frame: float,
        min_intersection_dv: float,
        min_intersection_relative_dv: float,
        min_intersection_angle_deg: float,
        min_intersection_angle_speed: float,
        min_track_bbox_perimeter: float,
        frame_center_x: float,
        min_primary_centering_delta: float) -> tuple[list[SegmentBuilder], list[Track]]:
    """Find trajectory segments and tracks within one rally.

    Parameters
    ----------
    rally
        Rally metadata dictionary containing at least ``start_frame`` and
        ``stop_frame``.
    rally_id
        Integer rally index to attach to every surviving ``SegmentBuilder`` and
        ``Track``.
    detections_by_frame
        Ball-detection dictionaries grouped by frame, with frame zero equal to
        ``rally["start_frame"]``. Each detection is expected to contain ``frame``,
        ``cx``, ``cy``, and a unique ``id``.
    init_size
        Number of initial candidate detections required before pruning a new
        segment hypothesis.
    min_pruned_size
        Minimum number of detections a segment must retain after pruning.
    max_frame_gap
        Largest allowed frame gap between consecutive detections while building
        a segment.
    max_init_sep
        Maximum initial point separation, in pixels/sec, used to reject implausible
        initialization candidates.
    max_init_residual
        Maximum residual, in pixels, allowed when pruning the initial candidate set.
    max_dv
        Maximum allowed velocity-vector change, in pixels/sec, when extending an
        already pruned segment.
    min_length
        Minimum number of detections required for a segment to survive filtering.
    min_speed
        Minimum average segment speed, in pixels/sec.
    max_speed
        Maximum average segment speed, in pixels/sec.
    FPS
        Video frame rate in frames/sec.
    max_time_gap
        Maximum time gap, in seconds, between segment endpoints considered for
        track combination.
    max_intersection_residual
        Base maximum separation, in pixels, allowed between extrapolated segment
        endpoints when linking tracks.
    intersection_residual_per_sqrt_frame
        Additional allowed intersection residual, in pixels, multiplied by the
        square root of the frame gap between linked segments.
    min_intersection_dv
        Minimum absolute velocity-vector jump, in pixels/sec, required to store a
        linked-segment intersection as a genuine velocity discontinuity.
    min_intersection_relative_dv
        Minimum relative velocity-vector jump required to store a linked-segment
        intersection, measured as ``norm(v1 - v2) / max(norm(v1), norm(v2))``.
    min_intersection_angle_deg
        Minimum velocity direction change, in degrees, required to store a
        linked-segment intersection when both endpoint speeds pass the angle
        speed floor.
    min_intersection_angle_speed
        Minimum speed, in pixels/sec, required on both sides of a linked-segment
        intersection before applying ``min_intersection_angle_deg``.
    min_track_bbox_perimeter
        Minimum bounding-box perimeter, in pixels, for a combined track to survive.
    frame_center_x
        Horizontal image coordinate used to prefer primary-court tracks when two
        tracks overlap in time.
    min_primary_centering_delta
        Minimum difference, in pixels, in horizontal centering score required to
        discard the less-centered overlapping track.

    Returns
    -------
    tuple[list[SegmentBuilder], list[Track]]
        Surviving deduplicated segments and primary tracks for this rally.
    """

    segments = [ ]
    for frame, detections in enumerate(detections_by_frame):
        if len(detections) == 0:
            continue
        # Loop over segments and try to extend them with detections in this frame.
        for segment in segments:
            detections = segment.build(detections)
            if len(detections) == 0:
                break
        # Loop over detections in this frame to start new segments for any not already used.
        for point in detections:
            segment = SegmentBuilder(
                point, rally, rally_id,
                init_size, min_pruned_size, max_frame_gap, max_init_sep, max_init_residual, max_dv, FPS)
            segments.append(segment)

    # Remove any segments that were never successfully pruned to a viable set of init_size points.
    segments = [segment for segment in segments if segment.pruned]
    print(f"Found {len(segments)} segments after pruning")

    # Remove short segments.
    segments = [segment for segment in segments if len(segment.points) >= min_length]
    print(f"Found {len(segments)} segments after removing short segments")

    # Remove segments that are too slow or too fast.
    segments = [segment for segment in segments if min_speed <= segment.avg_speed() <= max_speed]
    print(f"Found {len(segments)} segments after removing slow/fast segments")

    # Remove duplicate detection ownership across surviving segment hypotheses.
    segments = deduplicate_segments(segments)
    print(f"Found {len(segments)} segments after deduplicating segments")

    tracks = combine_segments(
        segments, max_time_gap, max_intersection_residual,
        intersection_residual_per_sqrt_frame, min_intersection_dv,
        min_intersection_relative_dv,
        min_intersection_angle_deg, min_intersection_angle_speed,
        min_track_bbox_perimeter=min_track_bbox_perimeter)
    print(f"Combined {len(segments)} segments into {len(tracks)} tracks")

    tracks = get_primary_tracks(tracks, frame_center_x, min_primary_centering_delta)
    print(f"Selected {len(tracks)} primary tracks")

    return segments, tracks

