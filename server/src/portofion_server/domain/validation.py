"""Point parsing and validation for ingestion.

Hard errors abort the whole request (nothing is written). Soft warnings are
returned and persisted as point flags.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

MIN_DATE = date(1990, 1, 1)
MAX_ERRORS = 100


@dataclass
class Problems:
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def error(self, loc: str, code: str, message: str, d: str | None = None) -> None:
        if len(self.errors) < MAX_ERRORS:
            e = {"loc": loc, "code": code, "message": message}
            if d is not None:
                e["date"] = d
            self.errors.append(e)

    def warn(self, code: str, **kw) -> None:
        self.warnings.append({"code": code, **kw})


def max_allowed_date() -> date:
    return (datetime.now(UTC) + timedelta(hours=14)).date()


def parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str) or len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_points(raw: Any, loc: str, probs: Problems) -> list[tuple[date, float]]:
    """Accepts [[d, v], ...], [{"date": d, "value": v}, ...] or {d: v}. Returns sorted pairs."""
    pairs: list[tuple[Any, Any]] = []
    if raw is None:
        return []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        for i, item in enumerate(raw):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((item[0], item[1]))
            elif isinstance(item, dict) and "date" in item and "value" in item:
                pairs.append((item["date"], item["value"]))
            else:
                probs.error(f"{loc}[{i}]", "BAD_POINT",
                            "each point must be [date, value] or {date, value}")
    else:
        probs.error(loc, "BAD_POINTS", "points must be a list of [date, value] pairs or a {date: value} object")
        return []

    out: dict[date, float] = {}
    hi = max_allowed_date()
    for i, (d_raw, v) in enumerate(pairs):
        ploc = f"{loc}[{i}]"
        d = parse_date(d_raw)
        if d is None:
            probs.error(ploc, "BAD_DATE", f"date must be 'YYYY-MM-DD', got {d_raw!r}")
            continue
        ds = d.isoformat()
        if d < MIN_DATE or d > hi:
            probs.error(ploc, "DATE_OUT_OF_RANGE", f"date {ds} outside [1990-01-01, today]", ds)
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            if v is None:
                probs.error(ploc, "NULL_VALUE", "value is null; omit days without data", ds)
            else:
                probs.error(ploc, "BAD_VALUE", f"value must be a number, got {v!r}", ds)
            continue
        v = float(v)
        if not math.isfinite(v):
            probs.error(ploc, "NON_FINITE", "NaN/Inf not allowed; omit days without data", ds)
            continue
        if d in out:
            probs.error(ploc, "DUPLICATE_DATE", f"date {ds} appears more than once", ds)
            continue
        out[d] = v
    return sorted(out.items())


def parse_date_list(raw: Any, loc: str, probs: Problems) -> list[date]:
    out = []
    for i, d_raw in enumerate(raw or []):
        d = parse_date(d_raw)
        if d is None:
            probs.error(f"{loc}[{i}]", "BAD_DATE", f"date must be 'YYYY-MM-DD', got {d_raw!r}")
        else:
            out.append(d)
    return out


def check_returns(
    points: list[tuple[date, float]], loc: str, probs: Problems, outlier_abs: float, strict: bool
) -> list[dict]:
    """Validate simple returns. Returns flag rows (OUTLIER, PERCENT_UNITS, GAP)."""
    flags: list[dict] = []
    if not points:
        return flags
    for i, (d, v) in enumerate(points):
        if v <= -1.0:
            probs.error(f"{loc}[{i}]", "RETURN_BELOW_MINUS_ONE", f"return {v} <= -1 is impossible",
                        d.isoformat())
    vals = np.array([v for _, v in points])
    absv = np.abs(vals)
    if len(vals) >= 5 and (np.median(absv) > 0.05 or (absv > 1).mean() > 0.05):
        w = {"code": "PERCENT_UNITS", "series": "returns",
             "message": "returns look like percentages; send decimals (0.012 for 1.2%)"}
        if strict:
            probs.error(loc, "PERCENT_UNITS", w["message"])
        else:
            probs.warnings.append(w)
            flags.append({"d": points[0][0], "code": "PERCENT_UNITS", "value": float(np.median(absv))})
    for d, v in points:
        if abs(v) > outlier_abs:
            w = {"code": "OUTLIER", "series": "returns", "date": d.isoformat(), "value": v}
            if strict:
                probs.error(loc, "OUTLIER", f"|return| {v} exceeds {outlier_abs}", d.isoformat())
            else:
                probs.warnings.append(w)
                flags.append({"d": d, "code": "OUTLIER", "value": v})
    if len(points) >= 3:
        ds = np.array([p[0].toordinal() for p in points])
        gaps = np.diff(ds)
        med = float(np.median(gaps))
        limit = max(14.0, 5.0 * med)  # ordinary holiday weeks are not gaps
        for i in np.flatnonzero(gaps > limit):
            d = points[i + 1][0]
            probs.warnings.append({"code": "GAP", "series": "returns", "date": d.isoformat(),
                                   "days": int(gaps[i])})
            flags.append({"d": d, "code": "GAP", "value": float(gaps[i])})
    return flags


def nav_to_returns(
    points: list[tuple[date, float]],
    base_nav: float | None,
    loc: str,
    probs: Problems,
) -> tuple[list[tuple[date, float]], date | None]:
    """Convert NAV pairs to returns. Without an anchor the first point is dropped."""
    for i, (d, v) in enumerate(points):
        if v <= 0:
            probs.error(f"{loc}[{i}]", "NAV_NOT_POSITIVE", f"NAV {v} must be > 0", d.isoformat())
    if probs.errors or not points:
        return [], None
    out = []
    dropped = None
    if base_nav is not None:
        out.append((points[0][0], points[0][1] / base_nav - 1.0))
    else:
        dropped = points[0][0]
    for (_, prev), (d, v) in itertools.pairwise(points):
        out.append((d, v / prev - 1.0))
    return out, dropped
