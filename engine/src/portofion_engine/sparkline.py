"""Largest-Triangle-Three-Buckets downsampling for NAV sparklines."""

from __future__ import annotations

import numpy as np
import pandas as pd


def lttb(y: np.ndarray, n_out: int) -> np.ndarray:
    """Return the indices of ``n_out`` points chosen by LTTB (x = position)."""
    n = len(y)
    if n_out >= n or n_out < 3:
        return np.arange(n)
    idx = np.empty(n_out, dtype=np.int64)
    idx[0] = 0
    idx[-1] = n - 1
    bucket = (n - 2) / (n_out - 2)
    a = 0
    for i in range(n_out - 2):
        lo = int(np.floor(i * bucket)) + 1
        hi = min(int(np.floor((i + 1) * bucket)) + 1, n - 1)
        nlo = hi
        nhi = min(int(np.floor((i + 2) * bucket)) + 1, n)
        avg_x = (nlo + nhi - 1) / 2.0
        avg_y = float(np.mean(y[nlo:nhi])) if nhi > nlo else float(y[-1])
        xs = np.arange(lo, hi)
        areas = np.abs((a - avg_x) * (y[lo:hi] - y[a]) - (a - xs) * (avg_y - y[a]))
        a = lo + int(np.argmax(areas))
        idx[i + 1] = a
    return idx


def sparkline(r: pd.Series, n_out: int = 64) -> list[float]:
    if r.empty:
        return []
    nav = np.concatenate([[1.0], (1.0 + r.to_numpy()).cumprod()])
    pick = lttb(nav, n_out)
    return [round(float(v), 6) for v in nav[pick]]
