from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from portofion_server.db.models import Series, SeriesRevision
from portofion_server.errors import ApiError

DEFAULT_AGG = {
    "returns": "compound",
    "turnover": "sum",
    "gross_exposure": "mean",
    "net_exposure": "mean",
    "leverage": "mean",
    "positions": "last",
    "capacity": "last",
    "nav": "last",
}
EPS = 1e-12

# --- read path with a small LRU keyed by (series_id, data_rev) -----------------------------

_CACHE: OrderedDict[tuple[int, int], pd.Series] = OrderedDict()
_CACHE_MAX = 512
_LOCK = threading.Lock()


def _cache_get(key):
    with _LOCK:
        s = _CACHE.get(key)
        if s is not None:
            _CACHE.move_to_end(key)
        return s


def _cache_put(key, s):
    with _LOCK:
        _CACHE[key] = s
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _fetch(db: Session, ids: list[int]) -> dict[int, pd.Series]:
    if not ids:
        return {}
    rows = db.execute(
        text(
            "SELECT series_id, array_agg(d - DATE '1970-01-01' ORDER BY d), array_agg(value ORDER BY d) "
            "FROM series_points WHERE series_id = ANY(:ids) GROUP BY series_id"
        ),
        {"ids": ids},
    ).all()
    out = {}
    for sid, days, vals in rows:
        idx = pd.DatetimeIndex(np.array(days, dtype="datetime64[D]").astype("datetime64[ns]"))
        out[sid] = pd.Series(np.asarray(vals, dtype="float64"), index=idx)
    return out


def load_series(db: Session, series_rows: list[tuple[int, int]]) -> dict[int, pd.Series]:
    """Load points for ``[(series_id, data_rev), ...]``; returns {series_id: Series}."""
    out: dict[int, pd.Series] = {}
    missing = []
    for sid, rev in series_rows:
        hit = _cache_get((sid, rev))
        if hit is not None:
            out[sid] = hit
        else:
            missing.append((sid, rev))
    fetched = _fetch(db, [sid for sid, _ in missing])
    for sid, rev in missing:
        s = fetched.get(sid, pd.Series(dtype="float64", index=pd.DatetimeIndex([])))
        _cache_put((sid, rev), s)
        out[sid] = s
    return out


def series_rows_for_versions(db: Session, version_ids: list[int], name: str = "returns") -> dict[int, tuple[int, int]]:
    rows = db.execute(
        select(Series.version_id, Series.id, Series.data_rev).where(
            Series.version_id.in_(version_ids), Series.name == name
        )
    ).all()
    return {v: (sid, rev) for v, sid, rev in rows}


def load_returns(db: Session, version_ids: list[int]) -> dict[int, pd.Series]:
    """{version_id: returns series} (empty Series when a version has no returns yet)."""
    rows = series_rows_for_versions(db, list(version_ids))
    loaded = load_series(db, list(rows.values()))
    empty = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    return {v: loaded.get(rows[v][0], empty) if v in rows else empty for v in version_ids}


# --- write path ------------------------------------------------------------------------------


def get_or_create_series(db: Session, version_id: int, name: str, agg: str | None = None,
                         unit: str | None = None, lock: bool = True) -> Series:
    stmt = (
        pg_insert(Series)
        .values(version_id=version_id, name=name, agg=agg or DEFAULT_AGG.get(name, "last"), unit=unit)
        .on_conflict_do_nothing(index_elements=["version_id", "name"])
    )
    db.execute(stmt)
    q = select(Series).where(Series.version_id == version_id, Series.name == name)
    if lock:
        q = q.with_for_update()
    s = db.scalar(q.execution_options(populate_existing=True))
    if agg and s.agg != agg:
        s.agg = agg
    if unit and s.unit != unit:
        s.unit = unit
    return s


def _existing(db: Session, sid: int, lo: date | None, hi: date | None) -> dict[date, float]:
    if lo is None:
        q = text("SELECT d, value FROM series_points WHERE series_id = :sid")
        rows = db.execute(q, {"sid": sid}).all()
    else:
        q = text("SELECT d, value FROM series_points WHERE series_id = :sid AND d BETWEEN :lo AND :hi")
        rows = db.execute(q, {"sid": sid, "lo": lo, "hi": hi}).all()
    return {d: v for d, v in rows}


def write_points(
    db: Session,
    series: Series,
    points: list[tuple[date, float]],
    *,
    replace: bool = False,
    delete: list[date] | None = None,
    on_existing: str = "update",
    batch_id: int | None = None,
    today: date,
) -> dict:
    sid = series.id
    delete = sorted(set(delete or []))
    if replace:
        existing = _existing(db, sid, None, None)
    elif points:
        existing = _existing(db, sid, points[0][0], points[-1][0])
    else:
        existing = {}
    if delete and not replace:
        existing.update(_existing(db, sid, delete[0], delete[-1]))

    new = dict(points)
    inserts = [(d, v) for d, v in points if d not in existing]
    updates = [(d, existing[d], v) for d, v in points if d in existing and abs(existing[d] - v) > EPS]
    unchanged = len(points) - len(inserts) - len(updates)
    skipped = 0
    if updates and on_existing == "skip":
        skipped = len(updates)
        updates = []
    elif updates and on_existing == "error":
        raise ApiError(
            409, "POINT_CONFLICT", f"{len(updates)} existing points in '{series.name}' would change",
            hint="use on_existing='update' to overwrite (revisions are kept) or 'skip'",
            details=[{"date": d.isoformat(), "old": o, "new": n} for d, o, n in updates[:20]],
        )
    del_dates = set()
    if replace:
        del_dates |= {d for d in existing if d not in new}
    del_dates |= {d for d in delete if d in existing and d not in new}
    del_dates = sorted(del_dates)

    if inserts:
        db.execute(
            text(
                "INSERT INTO series_points (series_id, d, value, first_seen) "
                "SELECT :sid, d, v, :today FROM unnest(CAST(:ds AS date[]), CAST(:vs AS float8[])) AS u(d, v)"
            ),
            {"sid": sid, "today": today, "ds": [d for d, _ in inserts], "vs": [v for _, v in inserts]},
        )
    if updates:
        db.execute(
            text(
                "UPDATE series_points sp SET value = u.v "
                "FROM unnest(CAST(:ds AS date[]), CAST(:vs AS float8[])) AS u(d, v) "
                "WHERE sp.series_id = :sid AND sp.d = u.d"
            ),
            {"sid": sid, "ds": [d for d, _, _ in updates], "vs": [n for _, _, n in updates]},
        )
    if del_dates:
        db.execute(
            text("DELETE FROM series_points WHERE series_id = :sid AND d = ANY(CAST(:ds AS date[]))"),
            {"sid": sid, "ds": del_dates},
        )
    revs = [SeriesRevision(series_id=sid, d=d, old_value=o, new_value=n, batch_id=batch_id)
            for d, o, n in updates]
    revs += [SeriesRevision(series_id=sid, d=d, old_value=existing[d], new_value=None, batch_id=batch_id)
             for d in del_dates]
    if revs:
        db.add_all(revs)

    changed = bool(inserts or updates or del_dates)
    if changed:
        lo, hi, n = db.execute(
            text("SELECT min(d), max(d), count(*) FROM series_points WHERE series_id = :sid"), {"sid": sid}
        ).one()
        series.first_date, series.last_date, series.n_points = lo, hi, n
        series.data_rev = (series.data_rev or 0) + 1
        series.updated_at = func.now()
    db.flush()
    return {
        "inserted": len(inserts),
        "updated": len(updates),
        "unchanged": unchanged,
        "skipped": skipped,
        "deleted": len(del_dates),
        "first_date": series.first_date.isoformat() if series.first_date else None,
        "last_date": series.last_date.isoformat() if series.last_date else None,
        "n_points": series.n_points,
        "changed": changed,
    }


def latest_value_before(db: Session, sid: int, d: date) -> float | None:
    return db.execute(
        text("SELECT value FROM series_points WHERE series_id = :sid AND d < :d ORDER BY d DESC LIMIT 1"),
        {"sid": sid, "d": d},
    ).scalar()
