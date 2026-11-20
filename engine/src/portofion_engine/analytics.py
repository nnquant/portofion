"""JSON-ready analytics bundles built from the primitive modules."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np
import pandas as pd

from portofion_engine.calendar import align, window_slice
from portofion_engine.drawdown import drawdown_series, top_drawdowns
from portofion_engine.metrics import summary, trailing_returns
from portofion_engine.relative import corr_matrix, relative, rolling_corr
from portofion_engine.rolling import rolling_sharpe, rolling_vol
from portofion_engine.sparkline import sparkline
from portofion_engine.tables import monthly_table, yearly_returns


def _dates(idx: pd.DatetimeIndex) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in idx]


def _vals(s: pd.Series | np.ndarray, nd: int = 8) -> list[float | None]:
    arr = s.to_numpy() if isinstance(s, pd.Series) else s
    return [None if not np.isfinite(v) else round(float(v), nd) for v in arr]


def version_snapshot(
    r: pd.Series,
    *,
    periods_per_year: float,
    rf: float,
    var_level: float,
    live_since: date | None,
    windows: tuple[str, ...] = ("all", "live", "ytd", "1y", "3y"),
) -> dict:
    """What gets cached per version on ingest: per-window metrics plus summary fields."""
    metrics = {}
    for w in windows:
        sub = window_slice(r, w, live_since)
        metrics[w] = summary(sub, periods_per_year, rf, var_level)
    full = metrics["all"]
    return {
        "metrics": metrics,
        "summary": {
            "spark": sparkline(r, 64),
            **trailing_returns(r),
            "cur_dd": full.get("cur_dd"),
            "cur_dd_days": full.get("cur_dd_days"),
            "detail": {
                "monthly": monthly_table(r),
                "yearly": yearly_returns(r),
                "top_dd": [e.to_dict() for e in top_drawdowns(r, 5)],
            },
        },
    }


def analytics(
    r: pd.Series,
    *,
    periods_per_year: float = 252,
    rf: float = 0.0,
    var_level: float = 0.95,
    window: str = "all",
    live_since: date | None = None,
    rolling: int = 126,
    bench: pd.Series | None = None,
) -> dict:
    """Full detail payload for a single series."""
    sub = window_slice(r, window, live_since)
    nav = (1.0 + sub).cumprod()
    out = {
        "dates": _dates(sub.index),
        "returns": _vals(sub, 10),
        "nav": _vals(nav),
        "drawdown": _vals(drawdown_series(sub)),
        "rolling_sharpe": _vals(rolling_sharpe(sub, rolling, periods_per_year, rf), 4),
        "rolling_vol": _vals(rolling_vol(sub, rolling, periods_per_year), 6),
        "metrics": summary(sub, periods_per_year, rf, var_level),
        "monthly": monthly_table(sub),
        "top_dd": [e.to_dict() for e in top_drawdowns(sub, 5)],
        "relative": None,
    }
    if bench is not None and not bench.empty:
        out["relative"] = relative(sub, bench, periods_per_year)
    return out


def compare(
    series: Mapping[str, pd.Series],
    *,
    periods: Mapping[str, float],
    rf: float = 0.0,
    var_level: float = 0.95,
    start: date | None = None,
    end: date | None = None,
    corr_freq: str = "D",
    rolling: int = 63,
) -> dict:
    """Aligned compare bundle for N series."""
    keys = list(series)
    clipped = {}
    for k in keys:
        s = series[k]
        if start is not None:
            s = s[s.index >= pd.Timestamp(start)]
        if end is not None:
            s = s[s.index <= pd.Timestamp(end)]
        clipped[k] = s
    aligned, _ = align(clipped)
    nonempty = [clipped[k] for k in keys if not clipped[k].empty]
    common_start = max(s.index[0] for s in nonempty) if nonempty else None
    common_end = min(s.index[-1] for s in nonempty) if nonempty else None
    common = {}
    own = {}
    for k in keys:
        s = clipped[k]
        p = periods.get(k, 252)
        own[k] = summary(s, p, rf, var_level)
        if common_start is not None and common_end is not None and common_start <= common_end:
            common[k] = summary(s[(s.index >= common_start) & (s.index <= common_end)], p, rf, var_level)
        else:
            common[k] = summary(s.iloc[0:0], p, rf, var_level)
    roll = {}
    if len(keys) > 1 and not clipped[keys[0]].empty:
        first = clipped[keys[0]]
        for k in keys[1:]:
            rc = rolling_corr(first, clipped[k], rolling)
            rc = rc.reindex(aligned.index)
            roll[k] = _vals(rc, 4)
    return {
        "keys": keys,
        "dates": _dates(aligned.index),
        "returns": {k: _vals(aligned[k], 10) for k in keys},
        "common_start": common_start.date().isoformat() if common_start is not None else None,
        "common_end": common_end.date().isoformat() if common_end is not None else None,
        "metrics_common": common,
        "metrics_own": own,
        "corr": corr_matrix(clipped, corr_freq),
        "rolling_corr": roll,
    }
