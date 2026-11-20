from __future__ import annotations

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from portofion_engine import ENGINE_VERSION
from portofion_engine.analytics import version_snapshot
from portofion_server.db.models import (
    METRIC_COLUMNS,
    IngestBatch,
    PointFlag,
    Series,
    SeriesRevision,
    Strategy,
    Version,
    VersionMetrics,
    VersionSummary,
    Workspace,
)
from portofion_server.domain.common import metrics_settings_hash, periods_for, ws_settings
from portofion_server.domain.series_store import load_series

WINDOWS = ("all", "live", "ytd", "1y", "3y")


def recompute_version(db: Session, ws: Workspace, strategy: Strategy, version: Version) -> dict:
    """Recompute cached metrics + summary for one version. Returns the 'all' metrics."""
    s = ws_settings(ws)
    ret = db.scalar(select(Series).where(Series.version_id == version.id, Series.name == "returns"))
    data_rev = ret.data_rev if ret else 0
    if ret is not None:
        r = load_series(db, [(ret.id, ret.data_rev)])[ret.id]
    else:
        r = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    ppy = periods_for(ws, strategy)
    snap = version_snapshot(r, periods_per_year=ppy, rf=s["rf"], var_level=s["var_level"],
                            live_since=version.live_since, windows=WINDOWS)
    shash = metrics_settings_hash(ws, strategy, version.live_since)

    db.execute(delete(VersionMetrics).where(VersionMetrics.version_id == version.id))
    rows = []
    for w, m in snap["metrics"].items():
        row = {
            "version_id": version.id, "window": w, "engine_version": ENGINE_VERSION,
            "settings_hash": shash, "data_rev": data_rev, "n": m["n"],
            "first_date": m["first_date"], "last_date": m["last_date"],
        }
        for c in METRIC_COLUMNS:
            row[c] = m.get(c)
        rows.append(row)
    db.execute(pg_insert(VersionMetrics), rows)

    series_ids = select(Series.id).where(Series.version_id == version.id).scalar_subquery()
    n_rev = db.scalar(select(func.count()).select_from(SeriesRevision).where(SeriesRevision.series_id.in_(series_ids)))
    n_flags = db.scalar(
        select(func.count()).select_from(PointFlag).where(
            PointFlag.series_id.in_(series_ids), PointFlag.acked_at.is_(None)
        )
    )
    last_report = db.scalar(select(func.max(IngestBatch.received_at)).where(IngestBatch.version_id == version.id))
    sm = snap["summary"]
    values = {
        "version_id": version.id, "engine_version": ENGINE_VERSION, "settings_hash": shash,
        "data_rev": data_rev, "spark": sm["spark"], "ret_1d": sm["ret_1d"], "ret_mtd": sm["ret_mtd"],
        "ret_ytd": sm["ret_ytd"], "ret_1y": sm["ret_1y"], "cur_dd": sm["cur_dd"],
        "cur_dd_days": sm["cur_dd_days"], "n_revisions": n_rev or 0, "n_open_flags": n_flags or 0,
        "last_report_at": last_report, "detail": sm["detail"], "computed_at": func.now(),
    }
    stmt = pg_insert(VersionSummary).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["version_id"], set_={k: stmt.excluded[k] for k in values if k != "version_id"}
    )
    db.execute(stmt)
    return snap["metrics"]["all"]


def refresh_flag_counts(db: Session, version_id: int) -> None:
    series_ids = select(Series.id).where(Series.version_id == version_id).scalar_subquery()
    n_flags = db.scalar(
        select(func.count()).select_from(PointFlag).where(
            PointFlag.series_id.in_(series_ids), PointFlag.acked_at.is_(None)
        )
    )
    db.execute(
        VersionSummary.__table__.update()
        .where(VersionSummary.version_id == version_id)
        .values(n_open_flags=n_flags or 0)
    )


def recompute_workspace(db: Session, ws: Workspace, *, only_stale: bool = True) -> int:
    """Recompute every version whose cache keys are stale. Returns the count."""
    rows = db.execute(
        select(Version, Strategy)
        .join(Strategy, Strategy.id == Version.strategy_id)
        .where(Strategy.workspace_id == ws.id)
    ).all()
    n = 0
    current = {
        v.id: (sm.settings_hash, sm.engine_version, sm.data_rev)
        for v, sm in db.execute(
            select(Version, VersionSummary).join(VersionSummary, VersionSummary.version_id == Version.id)
            .join(Strategy, Strategy.id == Version.strategy_id).where(Strategy.workspace_id == ws.id)
        ).all()
    }
    revs = dict(db.execute(
        select(Series.version_id, Series.data_rev).join(Version, Version.id == Series.version_id)
        .join(Strategy, Strategy.id == Version.strategy_id)
        .where(Strategy.workspace_id == ws.id, Series.name == "returns")
    ).all())
    for v, st in rows:
        want = (metrics_settings_hash(ws, st, v.live_since), ENGINE_VERSION, revs.get(v.id, 0))
        if only_stale and current.get(v.id) == want:
            continue
        recompute_version(db, ws, st, v)
        n += 1
    return n
