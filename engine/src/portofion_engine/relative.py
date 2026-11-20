"""Pairwise statistics on the intersection of raw observations."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from portofion_engine.calendar import resample_compound
from portofion_engine.metrics import _f

MIN_OVERLAP = 20
MIN_OVERLAP_MATRIX = 60


def _pair(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
    idx = a.index.intersection(b.index)
    return a.loc[idx], b.loc[idx]


def relative(
    r: pd.Series, bench: pd.Series, periods_per_year: float = 252, min_overlap: int = MIN_OVERLAP
) -> dict:
    """beta, alpha (annual), tracking error, information ratio, correlation, n overlap."""
    a, b = _pair(r, bench)
    n = len(a)
    out = {"n_overlap": n, "beta": None, "alpha": None, "te": None, "ir": None, "corr": None}
    if n < min_overlap:
        return out
    vb = b.var(ddof=1)
    if vb > 1e-20:
        beta = a.cov(b) / vb
        out["beta"] = _f(beta)
        out["alpha"] = _f((a.mean() - beta * b.mean()) * periods_per_year)
    active = a - b
    te = active.std(ddof=1) * math.sqrt(periods_per_year)
    out["te"] = _f(te)
    if te > 1e-15:
        out["ir"] = _f(active.mean() * periods_per_year / te)
    out["corr"] = _f(a.corr(b))
    return out


def corr_matrix(
    series: Mapping[str, pd.Series], freq: str = "D", min_overlap: int | None = None
) -> dict:
    """Pairwise correlation on intersections. Returns ``{"keys", "corr", "n"}``."""
    if min_overlap is None:
        min_overlap = {"D": MIN_OVERLAP_MATRIX, "W": 26, "M": 12}.get(freq, MIN_OVERLAP_MATRIX)
    keys = list(series)
    rs = {k: resample_compound(series[k], freq) for k in keys}
    k = len(keys)
    corr = [[None] * k for _ in range(k)]
    cnt = [[0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i, k):
            a, b = _pair(rs[keys[i]], rs[keys[j]])
            n = len(a)
            cnt[i][j] = cnt[j][i] = n
            if i == j:
                corr[i][j] = 1.0 if n >= 2 else None
                continue
            if n >= min_overlap:
                c = _f(np.corrcoef(a.to_numpy(), b.to_numpy())[0, 1]) if a.std() > 0 and b.std() > 0 else None
                corr[i][j] = corr[j][i] = c
    return {"keys": keys, "corr": corr, "n": cnt}


def rolling_corr(a: pd.Series, b: pd.Series, window: int = 63) -> pd.Series:
    x, y = _pair(a, b)
    return x.rolling(window, min_periods=window).corr(y)
