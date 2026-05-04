"""Zero-mean normalised cross-correlation utilities for 1D curve alignment."""

import numpy as np
import scipy.signal


def zncc_best_lag(
    x,
    y,
    min_overlap_frac=0.5,
    method="auto",
    subpixel=False,
):
    """
    Find the integer lag where two 1D arrays have maximum zero-mean
    normalized cross-correlation (ZNCC).

    Parameters
    ----------
    x, y : array_like, shape (n,)
        Input 1D arrays. They may have different lengths, but they must
        be finite and non-empty.

    min_overlap_frac : float, optional
        Minimum required overlap as a fraction of min(len(x), len(y)).
        This prevents tiny-overlap lags from giving spuriously high ZNCC
        values near +/-1.

    method : {"auto", "direct", "fft"}, optional
        Passed directly to scipy.signal.correlate.

    subpixel : bool, optional
        If True, refine the best integer lag using a parabola through the
        ZNCC values at lags lag-1, lag, lag+1.

    Returns
    -------
    best_lag : int
        Lag index giving the largest ZNCC.

        Convention follows scipy.signal.correlate(x, y):

            lag < 0 means y is shifted to the right relative to x.
            lag > 0 means y is shifted to the left relative to x.

        For example, if the same feature is at x[50] and y[53],
        then best_lag should be approximately -3.

    best_similarity : float
        ZNCC similarity at best_lag, in [-1, +1].

            +1  : same shape after mean/scale normalization
             0  : no linear similarity
            -1  : inverted shape
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    if x.ndim != 1 or y.ndim != 1:
        raise ValueError("x and y must both be 1D arrays.")

    if x.size == 0 or y.size == 0:
        raise ValueError("x and y must both be non-empty.")

    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("x and y must contain only finite values.")

    if not (0 < min_overlap_frac <= 1):
        raise ValueError("min_overlap_frac must be in the interval (0, 1].")

    nx = x.size
    ny = y.size

    ox = np.ones(nx, dtype=np.float64)
    oy = np.ones(ny, dtype=np.float64)

    sum_xy = scipy.signal.correlate(x, y, mode="full", method=method)
    sum_x  = scipy.signal.correlate(x, oy, mode="full", method=method)
    sum_y  = scipy.signal.correlate(ox, y, mode="full", method=method)
    sum_x2 = scipy.signal.correlate(x * x, oy, mode="full", method=method)
    sum_y2 = scipy.signal.correlate(ox, y * y, mode="full", method=method)
    count  = scipy.signal.correlate(ox, oy, mode="full", method=method)

    lags = scipy.signal.correlation_lags(nx, ny, mode="full")

    numerator = sum_xy - (sum_x * sum_y) / count
    var_x = np.maximum(sum_x2 - (sum_x * sum_x) / count, 0.0)
    var_y = np.maximum(sum_y2 - (sum_y * sum_y) / count, 0.0)
    denom = np.sqrt(var_x * var_y)

    zncc = np.full_like(numerator, np.nan, dtype=np.float64)
    valid = denom > 0
    if not np.any(valid):
        return 0, 0.0

    zncc[valid] = numerator[valid] / denom[valid]
    zncc = np.clip(zncc, -1.0, 1.0)

    min_overlap = min_overlap_frac * min(nx, ny)
    valid &= count >= min_overlap
    if not np.any(valid):
        return 0, 0.0

    valid_indices = np.flatnonzero(valid)
    best_index = valid_indices[np.nanargmax(zncc[valid])]
    best_integer_lag = int(lags[best_index])
    best_lag = float(best_integer_lag)
    best_similarity = float(zncc[best_index])

    if subpixel:
        left = best_index - 1
        right = best_index + 1
        can_interpolate = (
            left >= 0
            and right < zncc.size
            and valid[left]
            and valid[right]
            and lags[left] == best_integer_lag - 1
            and lags[right] == best_integer_lag + 1
            and np.isfinite(zncc[left])
            and np.isfinite(zncc[best_index])
            and np.isfinite(zncc[right])
        )
        if can_interpolate:
            z_left, z_center, z_right = zncc[left], zncc[best_index], zncc[right]
            curvature = z_left - 2.0 * z_center + z_right
            if curvature < 0:
                delta = 0.5 * (z_left - z_right) / curvature
                if np.isfinite(delta) and abs(delta) <= 1.0:
                    best_lag = float(best_integer_lag + delta)
                    best_similarity = float(np.clip(
                        z_center - 0.25 * (z_left - z_right) * delta, -1.0, 1.0
                    ))

    return best_lag, best_similarity


def get_similarity(curves, min_overlap_frac=0.5, measure=True):
    """
    Compute pairwise ZNCC similarities between curves.

    Parameters
    ----------
    curves : ndarray, shape (nchunk, npts)
    min_overlap_frac : float
    measure : bool
        Whether to measure timing to select the best convolution method.

    Returns
    -------
    sim : ndarray, shape (nchunk, nchunk)
    lag : ndarray, shape (nchunk, nchunk)
    method : str
    """
    method, _ = scipy.signal.choose_conv_method(
        curves[0], curves[1], mode="full", measure=measure
    )
    nchunk, _ = curves.shape
    sim = np.full((nchunk, nchunk), np.nan)
    lag = np.full((nchunk, nchunk), np.nan)
    np.fill_diagonal(sim, 1.0)
    np.fill_diagonal(lag, 0.0)
    for i in range(nchunk - 1):
        for j in range(i + 1, nchunk):
            try:
                best_lag, best_sim = zncc_best_lag(
                    curves[i], curves[j],
                    min_overlap_frac=min_overlap_frac,
                    method=method,
                )
            except ValueError:
                best_sim = np.nan
                best_lag = 0.0
            sim[i, j] = sim[j, i] = best_sim
            lag[i, j] = lag[j, i] = best_lag
    return sim, lag, method


def shift_curve(curve, lag, *, fill_value=np.nan):
    """Shift `curve` by `lag` samples using linear interpolation."""
    curve = np.asarray(curve, dtype=float)
    i = np.arange(curve.size, dtype=float)
    return np.interp(i - lag, i, curve, left=fill_value, right=fill_value)


def robust_reference_curve(curves, clean_frac=0.9, min_overlap_frac=0.5, n_iter=3):
    """
    Build a robust reference curve from clean, shift-equivalent 1D curves.

    Parameters
    ----------
    curves : ndarray, shape (nchunk, npts)
    clean_frac : float
        Fraction of curves (by median pairwise similarity) used for alignment.
    min_overlap_frac : float
    n_iter : int
        Number of align-average iterations.

    Returns
    -------
    reference : ndarray, shape (npts,)
    lags : ndarray, shape (nchunk,)
        Lag of each curve relative to the reference (NaN for non-clean curves).
    similarities : ndarray, shape (nchunk,)
        ZNCC similarity to reference (NaN for non-clean curves).
    """
    curves = np.asarray(curves, dtype=float)
    if curves.ndim != 2:
        raise ValueError("curves must have shape (nchunk, npts).")

    nchunk, npts = curves.shape

    sim, _, method = get_similarity(curves, min_overlap_frac=min_overlap_frac, measure=True)
    sim_score = np.median(sim, axis=0)
    clean = sim_score >= np.quantile(sim_score, 1 - clean_frac)
    clean_indices = np.flatnonzero(clean)

    ref_index = clean_indices[np.argmax(sim_score[clean_indices])]
    reference = curves[ref_index].astype(float).copy()

    for _ in range(n_iter):
        aligned_list = []
        for idx in clean_indices:
            best_lag, _ = zncc_best_lag(
                reference, curves[idx],
                min_overlap_frac=min_overlap_frac,
                method=method,
            )
            aligned_list.append(shift_curve(curves[idx], best_lag))
        aligned = np.vstack(aligned_list)
        reference = np.nanmedian(aligned, axis=0)
        finite = np.isfinite(reference)
        if not np.all(finite):
            good_x = np.flatnonzero(finite)
            if good_x.size == 0:
                raise RuntimeError("Reference became all-NaN during alignment.")
            reference[~finite] = np.interp(
                np.flatnonzero(~finite), good_x, reference[finite]
            )

    lags = np.full(nchunk, np.nan)
    similarities = np.full(nchunk, np.nan)
    for idx in clean_indices:
        best_lag, best_sim = zncc_best_lag(
            reference, curves[idx],
            min_overlap_frac=min_overlap_frac,
            method=method,
            subpixel=True,
        )
        lags[idx] = best_lag
        similarities[idx] = best_sim

    # Estimate lag of the central chunk. Use interpolation in case the central chunk is not clean.
    lag0 = np.interp(nchunk // 2, np.arange(nchunk), lags)

    # Shift the reference curve by lag0 to align it with the central chunk.
    reference = shift_curve(reference, lag0)

    # Shift all lags by lag0 to be relative to the central chunk.
    lags -= lag0

    return reference, lags, similarities
