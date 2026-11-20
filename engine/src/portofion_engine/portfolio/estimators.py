"""Look-ahead-free estimators: row ``t`` only sees rows strictly before ``t``."""

from __future__ import annotations

import numpy as np
import pandas as pd


def prev_vol(aligned: pd.DataFrame, lookback: int) -> np.ndarray:
    """Std of the previous ``lookback`` aligned rows (NaN outside life skipped)."""
    return aligned.rolling(lookback, min_periods=2).std(ddof=1).shift(1).to_numpy()


def prev_obs_count(observed: pd.DataFrame, lookback: int) -> np.ndarray:
    """Number of real observations in the previous ``lookback`` rows."""
    return (
        observed.astype("float64").rolling(lookback, min_periods=1).sum().shift(1).fillna(0.0)
    ).to_numpy()


def window_cov(A: np.ndarray, t: int, lookback: int, cols: np.ndarray) -> np.ndarray | None:
    """Covariance of rows ``[t-lookback, t)`` for the selected columns.

    Uses a fast numpy path when the window is complete and falls back to
    pairwise-complete estimation otherwise. Missing pairs are treated as
    uncorrelated.
    """
    lo = max(0, t - lookback)
    X = A[lo:t][:, cols]
    if X.shape[0] < 3:
        return None
    if not np.isnan(X).any():
        return np.atleast_2d(np.cov(X, rowvar=False, ddof=1))
    df = pd.DataFrame(X)
    cov = df.cov(min_periods=max(3, X.shape[0] // 4)).to_numpy()
    var = df.var(ddof=1).to_numpy()
    cov = np.where(np.isnan(cov), 0.0, cov)
    np.fill_diagonal(cov, np.where(np.isnan(var), 0.0, var))
    return cov


def shrink_to_diag(cov: np.ndarray, delta: float) -> np.ndarray:
    d = np.diag(np.diag(cov))
    out = (1.0 - delta) * cov + delta * d
    # clip tiny/negative eigenvalues so the matrix is safely PD
    w, v = np.linalg.eigh((out + out.T) / 2.0)
    floor = max(1e-12 * float(np.trace(out)) / max(len(w), 1), 1e-18)
    w = np.maximum(w, floor)
    return (v * w) @ v.T
