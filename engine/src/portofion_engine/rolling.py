"""Rolling statistics over a fixed number of observations."""

from __future__ import annotations

import math

import pandas as pd


def rolling_vol(r: pd.Series, window: int, periods_per_year: float = 252) -> pd.Series:
    return r.rolling(window, min_periods=window).std(ddof=1) * math.sqrt(periods_per_year)


def rolling_sharpe(
    r: pd.Series, window: int, periods_per_year: float = 252, rf: float = 0.0
) -> pd.Series:
    rf_d = rf / periods_per_year
    roll = r.rolling(window, min_periods=window)
    sd = roll.std(ddof=1)
    out = (roll.mean() - rf_d) / sd * math.sqrt(periods_per_year)
    return out.where(sd > 1e-15)


def rolling_return(r: pd.Series, window: int) -> pd.Series:
    return (1.0 + r).rolling(window, min_periods=window).apply(lambda x: x.prod(), raw=True) - 1.0
