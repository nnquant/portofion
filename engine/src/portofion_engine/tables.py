"""Calendar return tables compounded from the series' own observations."""

from __future__ import annotations

import pandas as pd


def monthly_table(r: pd.Series) -> dict:
    """``{"years": [..], "rows": [[m1..m12, year_total], ..]}`` with None for empty months."""
    if r.empty:
        return {"years": [], "rows": []}
    g = (1.0 + r).groupby([r.index.year, r.index.month]).prod() - 1.0
    y = (1.0 + r).groupby(r.index.year).prod() - 1.0
    years = sorted(set(r.index.year))
    rows = []
    for yr in years:
        row = [float(g[(yr, m)]) if (yr, m) in g.index else None for m in range(1, 13)]
        row.append(float(y[yr]))
        rows.append(row)
    return {"years": years, "rows": rows}


def yearly_returns(r: pd.Series) -> dict[int, float]:
    if r.empty:
        return {}
    y = (1.0 + r).groupby(r.index.year).prod() - 1.0
    return {int(k): float(v) for k, v in y.items()}
