"""Drawdown series and episodes.

NAV is prefixed with NAV0 = 1 dated one day before the first observation, so a
loss on the first day counts as a drawdown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Episode:
    peak: pd.Timestamp
    trough: pd.Timestamp
    recovery: pd.Timestamp | None
    depth: float
    days: int  # calendar days from peak to recovery (or to the last date if open)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("peak", "trough", "recovery"):
            d[k] = d[k].date().isoformat() if d[k] is not None else None
        return d


def _nav_with_origin(r: pd.Series) -> pd.Series:
    origin = r.index[0] - pd.Timedelta(days=1)
    nav = (1.0 + r).cumprod()
    return pd.concat([pd.Series([1.0], index=[origin]), nav])


def drawdown_series(r: pd.Series) -> pd.Series:
    """Underwater curve (NAV / running max - 1) on the observation dates."""
    if r.empty:
        return r.copy()
    nav = _nav_with_origin(r)
    dd = nav / nav.cummax() - 1.0
    return dd.iloc[1:]


def episodes(r: pd.Series) -> list[Episode]:
    if r.empty:
        return []
    nav = _nav_with_origin(r)
    values = nav.to_numpy()
    idx = nav.index
    peak_val = values[0]
    peak_i = 0
    out: list[Episode] = []
    in_dd = False
    trough_i = 0
    for i in range(1, len(values)):
        v = values[i]
        if v >= peak_val:
            if in_dd:
                depth = values[trough_i] / peak_val - 1.0
                out.append(Episode(idx[peak_i], idx[trough_i], idx[i], float(depth),
                                   int((idx[i] - idx[peak_i]).days)))
                in_dd = False
            peak_val = v
            peak_i = i
        else:
            if not in_dd:
                in_dd = True
                trough_i = i
            elif v < values[trough_i]:
                trough_i = i
    if in_dd:
        depth = values[trough_i] / peak_val - 1.0
        out.append(Episode(idx[peak_i], idx[trough_i], None, float(depth),
                           int((idx[-1] - idx[peak_i]).days)))
    return out


def top_drawdowns(r: pd.Series, n: int = 5) -> list[Episode]:
    return sorted(episodes(r), key=lambda e: e.depth)[:n]


def drawdown_stats(r: pd.Series) -> dict:
    """max_dd, max_dd_days, longest_dd_days, cur_dd, cur_dd_days."""
    if r.empty:
        return {"max_dd": None, "max_dd_days": None, "longest_dd_days": None,
                "cur_dd": None, "cur_dd_days": None}
    eps = episodes(r)
    dd = drawdown_series(r)
    max_dd = float(min(dd.min(), 0.0))
    if eps:
        deepest = min(eps, key=lambda e: e.depth)
        max_dd_days = deepest.days
        longest = max(e.days for e in eps)
    else:
        max_dd_days = 0
        longest = 0
    open_ep = eps[-1] if eps and eps[-1].recovery is None else None
    return {
        "max_dd": max_dd,
        "max_dd_days": max_dd_days,
        "longest_dd_days": longest,
        "cur_dd": float(dd.iloc[-1]) if np.isfinite(dd.iloc[-1]) else None,
        "cur_dd_days": open_ep.days if open_ep else 0,
    }
