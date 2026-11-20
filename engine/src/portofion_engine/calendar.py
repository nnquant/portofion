"""Calendar handling: normalization, alignment across series, windows, resampling.

Alignment rule (see docs/metrics.md):
  * O_i  = dates with an observation for series i
  * L_i  = [min O_i, max O_i]  (life)
  * U    = union of all O_i
  aligned_i(t) = observed value   if t in O_i
               = 0.0              if t in L_i but not observed (NAV carried)
               = NaN              outside L_i (not alive)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np
import pandas as pd

WINDOWS = ("all", "live", "ytd", "mtd", "1y", "3y", "5y")


def as_returns(obj, *, name: str | None = None) -> pd.Series:
    """Normalize anything series-like into a clean float64 return series."""
    if isinstance(obj, pd.Series):
        s = obj.copy()
    elif isinstance(obj, Mapping):
        s = pd.Series(obj, dtype="float64")
    else:
        pairs = list(obj)
        s = pd.Series([v for _, v in pairs], index=[d for d, _ in pairs], dtype="float64")
    s.index = pd.DatetimeIndex(pd.to_datetime(s.index)).normalize()
    s = s.astype("float64")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[np.isfinite(s.to_numpy())]
    if name is not None:
        s.name = name
    return s


def align(
    series: Mapping[str, pd.Series],
    start: pd.Timestamp | date | None = None,
    end: pd.Timestamp | date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Align series onto the union calendar.

    Returns ``(aligned, observed)`` where ``aligned`` follows the fill rule above and
    ``observed`` is a boolean frame marking real observations.
    """
    keys = list(series)
    if not keys:
        empty = pd.DataFrame(index=pd.DatetimeIndex([]))
        return empty, empty.astype(bool)
    union = pd.DatetimeIndex([])
    for s in series.values():
        union = union.union(s.index)
    if start is not None:
        union = union[union >= pd.Timestamp(start)]
    if end is not None:
        union = union[union <= pd.Timestamp(end)]
    aligned = pd.DataFrame(index=union, columns=keys, dtype="float64")
    observed = pd.DataFrame(False, index=union, columns=keys)
    for k in keys:
        s = series[k]
        if s.empty:
            continue
        col = s.reindex(union)
        obs = col.notna()
        life = (union >= s.index[0]) & (union <= s.index[-1])
        col = col.where(~(life & ~obs.to_numpy()), 0.0)
        aligned[k] = col
        observed[k] = obs.to_numpy()
    return aligned, observed


def nav(r: pd.Series, base: float = 1.0) -> pd.Series:
    return base * (1.0 + r).cumprod()


def infer_periods_per_year(index: pd.DatetimeIndex) -> float | None:
    """Rough observations-per-year estimate from the calendar span."""
    if len(index) < 20:
        return None
    span_days = (index[-1] - index[0]).days
    if span_days <= 0:
        return None
    return (len(index) - 1) / (span_days / 365.25)


def window_slice(
    r: pd.Series, window: str = "all", live_since: date | pd.Timestamp | None = None
) -> pd.Series:
    """Trailing window anchored at the series' own last date."""
    if r.empty or window == "all":
        return r
    last = r.index[-1]
    if window == "live":
        if live_since is None:
            return r.iloc[0:0]
        return r[r.index >= pd.Timestamp(live_since)]
    if window == "ytd":
        return r[r.index >= pd.Timestamp(year=last.year, month=1, day=1)]
    if window == "mtd":
        return r[r.index >= pd.Timestamp(year=last.year, month=last.month, day=1)]
    years = {"1y": 1, "3y": 3, "5y": 5}.get(window)
    if years is None:
        raise ValueError(f"unknown window {window!r}")
    return r[r.index > last - pd.DateOffset(years=years)]


def resample_compound(r: pd.Series, freq: str) -> pd.Series:
    """Compound a return series over its own observations into W or M buckets."""
    if freq in (None, "D"):
        return r
    rule = {"W": "W-FRI", "M": "ME"}[freq]
    out = (1.0 + r).resample(rule).prod() - 1.0
    counts = r.resample(rule).count()
    return out[counts > 0]
