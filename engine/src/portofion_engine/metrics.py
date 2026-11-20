"""Scalar performance metrics.

Conventions follow oaks_cta.metrics (arithmetic annualization, rf=0 by default,
sample std). Undefined metrics are ``None`` rather than a 0.0 sentinel.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from portofion_engine.drawdown import drawdown_stats

METRIC_KEYS = (
    "n", "first_date", "last_date",
    "total_ret", "ann_ret", "cagr", "ann_vol", "sharpe", "sortino",
    "max_dd", "max_dd_days", "longest_dd_days", "cur_dd", "cur_dd_days", "calmar",
    "win_rate", "profit_factor", "skew", "kurt", "var", "cvar", "best", "worst",
)


def _f(x) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def ann_ret(r: pd.Series, periods_per_year: float = 252) -> float | None:
    return _f(r.mean() * periods_per_year) if len(r) else None


def cagr(r: pd.Series, periods_per_year: float = 252) -> float | None:
    if not len(r):
        return None
    end = float((1.0 + r).prod())
    if end <= 0:
        return -1.0
    return _f(end ** (periods_per_year / len(r)) - 1.0)


def ann_vol(r: pd.Series, periods_per_year: float = 252) -> float | None:
    if len(r) < 2:
        return None
    return _f(r.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe(r: pd.Series, periods_per_year: float = 252, rf: float = 0.0) -> float | None:
    if len(r) < 2:
        return None
    sd = r.std(ddof=1)
    if not sd or not math.isfinite(sd) or sd < 1e-15:
        return None
    return _f((r.mean() - rf / periods_per_year) / sd * math.sqrt(periods_per_year))


def sortino(r: pd.Series, periods_per_year: float = 252, rf: float = 0.0) -> float | None:
    if len(r) < 2:
        return None
    rf_d = rf / periods_per_year
    downside = np.minimum(r.to_numpy() - rf_d, 0.0)
    dd = math.sqrt(float(np.mean(downside**2)))
    if dd < 1e-15:
        return None
    return _f((r.mean() - rf_d) / dd * math.sqrt(periods_per_year))


def win_rate(r: pd.Series) -> float | None:
    nz = r[r != 0]
    return _f((nz > 0).mean()) if len(nz) else None


def profit_factor(r: pd.Series) -> float | None:
    neg = -r[r < 0].sum()
    if neg <= 0:
        return None
    return _f(r[r > 0].sum() / neg)


def var_cvar(r: pd.Series, level: float = 0.95) -> tuple[float | None, float | None]:
    if len(r) < 2:
        return None, None
    q = float(np.quantile(r.to_numpy(), 1.0 - level, method="linear"))
    tail = r[r <= q]
    return _f(q), _f(tail.mean()) if len(tail) else None


def summary(
    r: pd.Series,
    periods_per_year: float = 252,
    rf: float = 0.0,
    var_level: float = 0.95,
) -> dict:
    """Every scalar metric for one return series (its own observations only)."""
    n = len(r)
    out: dict = dict.fromkeys(METRIC_KEYS)
    out["n"] = n
    if n == 0:
        return out
    out["first_date"] = r.index[0].date().isoformat()
    out["last_date"] = r.index[-1].date().isoformat()
    out["total_ret"] = _f((1.0 + r).prod() - 1.0)
    out["ann_ret"] = ann_ret(r, periods_per_year)
    out["cagr"] = cagr(r, periods_per_year)
    out["ann_vol"] = ann_vol(r, periods_per_year)
    out["sharpe"] = sharpe(r, periods_per_year, rf)
    out["sortino"] = sortino(r, periods_per_year, rf)
    out.update(drawdown_stats(r))
    md = out["max_dd"]
    out["calmar"] = _f(out["ann_ret"] / abs(md)) if md and out["ann_ret"] is not None else None
    out["win_rate"] = win_rate(r)
    out["profit_factor"] = profit_factor(r)
    out["skew"] = _f(r.skew()) if n >= 3 else None
    out["kurt"] = _f(r.kurt()) if n >= 4 else None
    out["var"], out["cvar"] = var_cvar(r, var_level)
    out["best"] = _f(r.max())
    out["worst"] = _f(r.min())
    return out


def period_return(r: pd.Series, since: pd.Timestamp) -> float | None:
    sub = r[r.index >= since]
    if sub.empty:
        return None
    return _f((1.0 + sub).prod() - 1.0)


def trailing_returns(r: pd.Series) -> dict:
    """1d / mtd / ytd / 1y compounded returns anchored at the last date."""
    if r.empty:
        return {"ret_1d": None, "ret_mtd": None, "ret_ytd": None, "ret_1y": None}
    last = r.index[-1]
    return {
        "ret_1d": _f(r.iloc[-1]),
        "ret_mtd": period_return(r, pd.Timestamp(year=last.year, month=last.month, day=1)),
        "ret_ytd": period_return(r, pd.Timestamp(year=last.year, month=1, day=1)),
        "ret_1y": period_return(r, last - pd.DateOffset(years=1) + pd.Timedelta(days=1)),
    }
