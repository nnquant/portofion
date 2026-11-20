from __future__ import annotations

import numpy as np
import pandas as pd


def scheduled_mask(index: pd.DatetimeIndex, freq: str) -> np.ndarray:
    """True on the first calendar date of each period present in ``index``."""
    n = len(index)
    if n == 0:
        return np.zeros(0, dtype=bool)
    if freq == "daily":
        return np.ones(n, dtype=bool)
    if freq == "none":
        return np.zeros(n, dtype=bool)
    if freq == "weekly":
        iso = index.isocalendar()
        key = iso["year"].to_numpy() * 100 + iso["week"].to_numpy()
    elif freq == "monthly":
        key = index.year.to_numpy() * 100 + index.month.to_numpy()
    elif freq == "quarterly":
        key = index.year.to_numpy() * 10 + index.quarter.to_numpy()
    else:
        raise ValueError(f"unknown rebalance frequency {freq!r}")
    mask = np.ones(n, dtype=bool)
    mask[1:] = key[1:] != key[:-1]
    return mask
