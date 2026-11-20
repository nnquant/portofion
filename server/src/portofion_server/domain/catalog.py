"""Read/edit operations on the strategy catalog."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from portofion_engine import ENGINE_VERSION
from portofion_engine.analytics import analytics as engine_analytics
from portofion_server.auth.deps import WsCtx
from portofion_server.db.models import (
    METRIC_COLUMNS,
    ApiKey,
    Branch,
    IngestBatch,
    PointFlag,
    Portfolio,
    PortfolioMember,
    Scalar,
    Series,
    SeriesRevision,
    Strategy,
    StrategyTag,
    Tag,
    User,
    Version,
)
from portofion_server.domain.common import (
    audit,
    check_slug,
    metrics_settings_hash,
    periods_for,
    stable_hash,
    ws_settings,
)
from portofion_server.domain.ingest import ensure_tags, set_head
from portofion_server.domain.metrics_cache import recompute_version, refresh_flag_counts
from portofion_server.domain.refs import resolve
from portofion_server.domain.series_store import load_returns, load_series
from portofion_server.errors import ApiError, not_found
from portofion_server.schemas import BranchPatch, StrategyPatch, VersionPatch

LIB_METRICS = ("n", "first_date", "last_date", *METRIC_COLUMNS)


def _iso(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def tags_by_strategy(db: Session, ws_id: int) -> dict[int, list[str]]:
    rows = db.execute(
        select(StrategyTag.strategy_id, Tag.name).join(Tag, Tag.id == StrategyTag.tag_id)
        .where(Tag.workspace_id == ws_id).order_by(Tag.name)
    ).all()
    out: dict[int, list[str]] = defaultdict(list)
    for sid, name in rows:
        out[sid].append(name)
    return out


def tag_colors(db: Session, ws_id: int) -> dict[str, str | None]:
    return dict(db.execute(select(Tag.name, Tag.color).where(Tag.workspace_id == ws_id)).all())


def library(db: Session, ctx: WsCtx, *, level: str = "branch", window: str = "all",
            archived: bool = False, benchmarks: bool = True) -> dict:
    join = "v.id = b.head_version_id" if level == "branch" else "v.branch_id = b.id"
    where = ["s.workspace_id = :ws"]
    if not archived:
        where += ["s.archived_at IS NULL", "b.archived_at IS NULL", "v.archived_at IS NULL"]
    if not benchmarks:
        where.append("s.kind = 'strategy'")
    metric_cols = ", ".join(f"m.{c} AS m_{c}" for c in LIB_METRICS)
    sql = f"""
        SELECT s.id AS sid, s.slug, s.name, s.kind, s.archived_at AS s_arch, s.default_branch_id,
               b.id AS bid, b.name AS branch, b.head_version_id, b.head_locked, b.archived_at AS b_arch,
               v.id AS vid, v.label, v.seq, v.stage, v.live_since, v.code_ref, v.created_at AS v_created,
               v.archived_at AS v_arch,
               {metric_cols},
               sm.spark, sm.ret_1d, sm.ret_mtd, sm.ret_ytd, sm.ret_1y, sm.n_open_flags, sm.n_revisions,
               sm.last_report_at, sm.settings_hash, sm.engine_version,
               (SELECT count(*) FROM versions vv WHERE vv.branch_id = b.id) AS n_versions
        FROM strategies s
        JOIN branches b ON b.strategy_id = s.id
        JOIN versions v ON {join}
        LEFT JOIN version_metrics m ON m.version_id = v.id AND m."window" = :window
        LEFT JOIN version_summary sm ON sm.version_id = v.id
        WHERE {" AND ".join(where)}
        ORDER BY s.slug, (b.id = s.default_branch_id) DESC, b.name, v.seq DESC
    """
    rows = db.execute(text(sql), {"ws": ctx.ws_id, "window": window}).mappings().all()
    tags = tags_by_strategy(db, ctx.ws_id)
    vids = [r["vid"] for r in rows]
    scal: dict[int, dict] = defaultdict(dict)
    if vids:
        for vid, name, value in db.execute(
            select(Scalar.version_id, Scalar.name, Scalar.value).where(Scalar.version_id.in_(vids))
        ).all():
            scal[vid][name] = value
    out = []
    for r in rows:
        out.append({
            "strategy": r["slug"], "name": r["name"], "kind": r["kind"], "tags": tags.get(r["sid"], []),
            "branch": r["branch"], "is_default": r["bid"] == r["default_branch_id"],
            "is_head": r["vid"] == r["head_version_id"], "head_locked": r["head_locked"],
            "version": r["label"], "version_id": r["vid"], "seq": r["seq"], "n_versions": r["n_versions"],
            "stage": r["stage"], "live_since": _iso(r["live_since"]), "code_ref": r["code_ref"],
            "created_at": _iso(r["v_created"]),
            "archived": bool(r["s_arch"] or r["b_arch"] or r["v_arch"]),
            "m": {c: _iso(r[f"m_{c}"]) for c in LIB_METRICS},
            "spark": list(r["spark"]) if r["spark"] is not None else [],
            "ret_1d": r["ret_1d"], "ret_mtd": r["ret_mtd"], "ret_ytd": r["ret_ytd"], "ret_1y": r["ret_1y"],
            "flags": r["n_open_flags"] or 0, "revisions": r["n_revisions"] or 0,
            "last_report_at": _iso(r["last_report_at"]),
            "stale_cache": r["engine_version"] is not None and r["engine_version"] != ENGINE_VERSION,
            "scalars": scal.get(r["vid"], {}),
        })
    scalar_names = sorted({k for r in out for k in r["scalars"]})
    all_tags = sorted({t for ts in tags.values() for t in ts})
    colors = tag_colors(db, ctx.ws_id)
    return {"level": level, "window": window, "rows": out, "scalar_names": scalar_names, "tags": all_tags,
            "tag_colors": {t: colors.get(t) for t in all_tags},
            "settings": ws_settings(ctx.ws), "today": datetime.now(UTC).date().isoformat()}


def search(db: Session, ctx: WsCtx, q: str, limit: int = 30) -> list[dict]:
    like = f"%{q.strip()}%"
    out = []
    rows = db.execute(
        select(Strategy.slug, Strategy.name, Strategy.kind).where(
            Strategy.workspace_id == ctx.ws_id, or_(Strategy.slug.ilike(like), Strategy.name.ilike(like))
        ).order_by(Strategy.slug).limit(limit)
    ).all()
    out += [{"kind": k, "ref": s, "label": n} for s, n, k in rows]
    rows = db.execute(
        select(Strategy.slug, Branch.name).join(Branch, Branch.strategy_id == Strategy.id).where(
            Strategy.workspace_id == ctx.ws_id,
            or_(Strategy.slug.ilike(like), Branch.name.ilike(like)),
        ).order_by(Strategy.slug, Branch.name).limit(limit)
    ).all()
    out += [{"kind": "branch", "ref": f"{s}/{b}", "label": f"{s}/{b}"} for s, b in rows]
    rows = db.execute(
        select(Strategy.slug, Branch.name, Version.label).join(Branch, Branch.strategy_id == Strategy.id)
        .join(Version, Version.branch_id == Branch.id).where(
            Strategy.workspace_id == ctx.ws_id,
            or_(Strategy.slug.ilike(like), Version.label.ilike(like), Branch.name.ilike(like)),
        ).order_by(Strategy.slug, Branch.name, Version.seq.desc()).limit(limit)
    ).all()
    out += [{"kind": "version", "ref": f"{s}/{b}@{v}", "label": f"{s}/{b}@{v}"} for s, b, v in rows]
    rows = db.execute(
        select(Portfolio.slug, Portfolio.name).where(
            Portfolio.workspace_id == ctx.ws_id, or_(Portfolio.slug.ilike(like), Portfolio.name.ilike(like))
        ).limit(limit)
    ).all()
    out += [{"kind": "portfolio", "ref": f"pf:{s}", "label": n} for s, n in rows]
    return out


# --- strategy detail ---------------------------------------------------------------------------


def get_strategy(db: Session, ctx: WsCtx, slug: str) -> Strategy:
    st = db.scalar(select(Strategy).where(Strategy.workspace_id == ctx.ws_id, Strategy.slug == slug))
    if st is None:
        raise not_found("strategy")
    return st


def get_version(db: Session, ctx: WsCtx, version_id: int) -> tuple[Version, Strategy]:
    row = db.execute(
        select(Version, Strategy).join(Strategy, Strategy.id == Version.strategy_id)
        .where(Version.id == version_id, Strategy.workspace_id == ctx.ws_id)
    ).first()
    if row is None:
        raise not_found("version")
    return row[0], row[1]


def strategy_detail(db: Session, ctx: WsCtx, slug: str) -> dict:
    st = get_strategy(db, ctx, slug)
    branches = db.scalars(select(Branch).where(Branch.strategy_id == st.id).order_by(Branch.id)).all()
    versions = db.scalars(select(Version).where(Version.strategy_id == st.id).order_by(Version.seq.desc())).all()
    vids = [v.id for v in versions]
    metrics = {}
    summaries = {}
    series_by_v: dict[int, list] = defaultdict(list)
    scal: dict[int, dict] = defaultdict(dict)
    if vids:
        for r in db.execute(text(
            'SELECT * FROM version_metrics WHERE version_id = ANY(:ids) AND "window" = \'all\''
        ), {"ids": vids}).mappings():
            metrics[r["version_id"]] = {c: _iso(r[c]) for c in LIB_METRICS}
        for r in db.execute(text(
            "SELECT version_id, spark, ret_1d, ret_mtd, ret_ytd, ret_1y, n_open_flags, n_revisions, "
            "last_report_at FROM version_summary WHERE version_id = ANY(:ids)"), {"ids": vids}).mappings():
            summaries[r["version_id"]] = {k: _iso(v) for k, v in r.items() if k != "version_id"}
        for s in db.scalars(select(Series).where(Series.version_id.in_(vids)).order_by(Series.name)):
            series_by_v[s.version_id].append({
                "name": s.name, "agg": s.agg, "unit": s.unit, "n_points": s.n_points,
                "first_date": _iso(s.first_date), "last_date": _iso(s.last_date),
            })
        for sc in db.scalars(select(Scalar).where(Scalar.version_id.in_(vids))):
            scal[sc.version_id][sc.name] = {"value": sc.value, "as_of": _iso(sc.as_of)}
    by_branch: dict[int, list] = defaultdict(list)
    for v in versions:
        by_branch[v.branch_id].append({
            "id": v.id, "label": v.label, "seq": v.seq, "stage": v.stage, "live_since": _iso(v.live_since),
            "code_ref": v.code_ref, "code_repo": v.code_repo, "params": v.params, "description": v.description,
            "meta": v.meta, "created_at": _iso(v.created_at), "archived": v.archived_at is not None,
            "metrics": metrics.get(v.id), "summary": summaries.get(v.id),
            "series": series_by_v.get(v.id, []), "scalars": scal.get(v.id, {}),
        })
    tags = tags_by_strategy(db, ctx.ws_id).get(st.id, [])
    colors = tag_colors(db, ctx.ws_id)
    return {
        "strategy": {
            "id": st.id, "slug": st.slug, "name": st.name, "kind": st.kind, "description": st.description,
            "tags": tags, "tag_colors": {t: colors.get(t) for t in tags}, "periods_per_year": st.periods_per_year,
            "effective_periods_per_year": periods_for(ctx.ws, st),
            "default_branch_id": st.default_branch_id, "meta": st.meta,
            "created_at": _iso(st.created_at), "archived": st.archived_at is not None,
        },
        "branches": [{
            "id": b.id, "name": b.name, "head_version_id": b.head_version_id, "head_locked": b.head_locked,
            "forked_from_version_id": b.forked_from_version_id, "archived": b.archived_at is not None,
            "is_default": b.id == st.default_branch_id, "versions": by_branch.get(b.id, []),
        } for b in branches],
        "settings": ws_settings(ctx.ws),
    }


# --- analytics & raw series -------------------------------------------------------------------


def version_analytics(db: Session, ctx: WsCtx, version_id: int, *, window: str = "all",
                      rolling: int = 126, benchmark: str | None = None) -> tuple[dict, str]:
    v, st = get_version(db, ctx, version_id)
    s = ws_settings(ctx.ws)
    r = load_returns(db, [v.id])[v.id]
    bench = None
    bench_tag = None
    if benchmark:
        from portofion_server.domain.compare import load_ref_series

        bench, _, bench_tag = load_ref_series(db, ctx, benchmark)
    ret_rev = db.scalar(select(Series.data_rev).where(Series.version_id == v.id, Series.name == "returns")) or 0
    etag = stable_hash([ENGINE_VERSION, ret_rev, metrics_settings_hash(ctx.ws, st, v.live_since),
                        window, rolling, benchmark, bench_tag])
    out = engine_analytics(r, periods_per_year=periods_for(ctx.ws, st), rf=s["rf"], var_level=s["var_level"],
                           window=window, live_since=v.live_since, rolling=rolling, bench=bench)
    out["version_id"] = v.id
    return out, etag


def version_series(db: Session, ctx: WsCtx, version_id: int, names: list[str] | None,
                   fmt: str = "json"):
    v, _ = get_version(db, ctx, version_id)
    q = select(Series).where(Series.version_id == v.id)
    if names:
        q = q.where(Series.name.in_(names))
    rows = db.scalars(q.order_by(Series.name)).all()
    loaded = load_series(db, [(s.id, s.data_rev) for s in rows])
    frame = pd.DataFrame({s.name: loaded[s.id] for s in rows})
    frame = frame.sort_index()
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", *frame.columns])
        for d, row in frame.iterrows():
            w.writerow([d.strftime("%Y-%m-%d"), *["" if pd.isna(x) else repr(float(x)) for x in row]])
        return buf.getvalue()
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in frame.index],
        "series": {c: [None if pd.isna(x) else float(x) for x in frame[c]] for c in frame.columns},
        "meta": {s.name: {"agg": s.agg, "unit": s.unit} for s in rows},
    }


def version_revisions(db: Session, ctx: WsCtx, version_id: int, limit: int = 500) -> list[dict]:
    v, _ = get_version(db, ctx, version_id)
    rows = db.execute(
        select(SeriesRevision, Series.name).join(Series, Series.id == SeriesRevision.series_id)
        .where(Series.version_id == v.id).order_by(SeriesRevision.id.desc()).limit(limit)
    ).all()
    return [{"series": name, "date": _iso(r.d), "old": r.old_value, "new": r.new_value,
             "batch_id": r.batch_id, "at": _iso(r.created_at)} for r, name in rows]


def version_flags(db: Session, ctx: WsCtx, version_id: int) -> list[dict]:
    v, _ = get_version(db, ctx, version_id)
    rows = db.execute(
        select(PointFlag, Series.name).join(Series, Series.id == PointFlag.series_id)
        .where(Series.version_id == v.id).order_by(PointFlag.d.desc())
    ).all()
    return [{"series": name, "date": _iso(f.d), "code": f.code, "value": f.value,
             "acked": f.acked_at is not None, "at": _iso(f.created_at)} for f, name in rows]


def ack_flags(db: Session, ctx: WsCtx, version_id: int, dates: list[str] | None, codes: list[str] | None) -> int:
    user = ctx.require_user("member")
    v, _ = get_version(db, ctx, version_id)
    sids = select(Series.id).where(Series.version_id == v.id).scalar_subquery()
    q = PointFlag.__table__.update().where(PointFlag.series_id.in_(sids), PointFlag.acked_at.is_(None))
    if dates:
        q = q.where(PointFlag.d.in_([date.fromisoformat(d) for d in dates]))
    if codes:
        q = q.where(PointFlag.code.in_(codes))
    n = db.execute(q.values(acked_at=func.now(), acked_by=user.id)).rowcount
    refresh_flag_counts(db, v.id)
    db.commit()
    return n


def version_batches(db: Session, ctx: WsCtx, version_id: int, limit: int = 200) -> list[dict]:
    v, _ = get_version(db, ctx, version_id)
    rows = db.execute(
        select(IngestBatch, ApiKey.name, User.email)
        .outerjoin(ApiKey, ApiKey.id == IngestBatch.api_key_id)
        .outerjoin(User, User.id == IngestBatch.user_id)
        .where(IngestBatch.version_id == v.id).order_by(IngestBatch.id.desc()).limit(limit)
    ).all()
    out = []
    for b, key_name, email in rows:
        ser = (b.response or {}).get("series", {})
        out.append({
            "id": b.id, "at": _iso(b.received_at), "key": key_name, "user": email, "client": b.client,
            "series": {k: {f: s.get(f) for f in ("inserted", "updated", "deleted", "unchanged")}
                       for k, s in ser.items()},
            "warnings": len((b.response or {}).get("warnings", [])),
        })
    return out


# --- edits ----------------------------------------------------------------------------------


def patch_strategy(db: Session, ctx: WsCtx, slug: str, p: StrategyPatch) -> tuple[list[int], list[int]]:
    """Returns (versions whose metrics changed, branches whose head moved)."""
    ctx.require_user("member")
    st = get_strategy(db, ctx, slug)
    before, after = {}, {}
    recompute = False
    moved: list[int] = []
    for f in ("name", "description", "kind"):
        v = getattr(p, f)
        if v is not None and getattr(st, f) != v:
            before[f], after[f] = getattr(st, f), v
            setattr(st, f, v)
    if p.clear_periods_per_year and st.periods_per_year is not None:
        before["periods_per_year"], after["periods_per_year"] = st.periods_per_year, None
        st.periods_per_year = None
        recompute = True
    elif p.periods_per_year is not None and st.periods_per_year != p.periods_per_year:
        before["periods_per_year"], after["periods_per_year"] = st.periods_per_year, p.periods_per_year
        st.periods_per_year = p.periods_per_year
        recompute = True
    if p.default_branch is not None:
        br = db.scalar(select(Branch).where(Branch.strategy_id == st.id, Branch.name == p.default_branch))
        if br is None:
            raise not_found("branch")
        if st.default_branch_id != br.id:
            before["default_branch_id"], after["default_branch_id"] = st.default_branch_id, br.id
            st.default_branch_id = br.id
            moved.append(br.id)
    if p.archived is not None:
        st.archived_at = datetime.now(UTC) if p.archived else None
        after["archived"] = p.archived
    if p.tags is not None:
        db.execute(delete(StrategyTag).where(StrategyTag.strategy_id == st.id))
        ensure_tags(db, ctx.ws_id, st.id, p.tags)
        after["tags"] = p.tags
    st.updated_at = func.now()
    audit(db, ctx.ws_id, ctx.principal, "strategy.update", "strategy", st.id, before, after)
    changed = []
    if recompute:
        for v in db.scalars(select(Version).where(Version.strategy_id == st.id)):
            recompute_version(db, ctx.ws, st, v)
            changed.append(v.id)
    db.commit()
    return changed, moved


def patch_branch(db: Session, ctx: WsCtx, slug: str, name: str, p: BranchPatch) -> list[int]:
    ctx.require_user("member")
    st = get_strategy(db, ctx, slug)
    br = db.scalar(select(Branch).where(Branch.strategy_id == st.id, Branch.name == name))
    if br is None:
        raise not_found("branch")
    moved = []
    if p.head is not None:
        v = db.scalar(select(Version).where(Version.branch_id == br.id, Version.label == p.head))
        if v is None:
            raise not_found("version")
        if br.head_version_id != v.id:
            audit(db, ctx.ws_id, ctx.principal, "branch.head", "branch", br.id,
                  {"head_version_id": br.head_version_id}, {"head_version_id": v.id})
            set_head(db, ctx, br, v.id, "manual")
            moved.append(br.id)
    if p.head_locked is not None and br.head_locked != p.head_locked:
        audit(db, ctx.ws_id, ctx.principal, "branch.lock", "branch", br.id,
              {"head_locked": br.head_locked}, {"head_locked": p.head_locked})
        br.head_locked = p.head_locked
    if p.archived is not None:
        br.archived_at = datetime.now(UTC) if p.archived else None
    db.commit()
    return moved


def patch_version(db: Session, ctx: WsCtx, version_id: int, p: VersionPatch) -> bool:
    ctx.require_user("member")
    v, st = get_version(db, ctx, version_id)
    before, after = {}, {}
    recompute = False
    if p.stage is not None and v.stage != p.stage:
        before["stage"], after["stage"] = v.stage, p.stage
        v.stage = p.stage
    if p.clear_live_since and v.live_since is not None:
        before["live_since"], after["live_since"] = _iso(v.live_since), None
        v.live_since = None
        recompute = True
    elif p.live_since is not None and v.live_since != p.live_since:
        before["live_since"], after["live_since"] = _iso(v.live_since), _iso(p.live_since)
        v.live_since = p.live_since
        recompute = True
    if p.description is not None:
        v.description = p.description
    if p.archived is not None:
        v.archived_at = datetime.now(UTC) if p.archived else None
        after["archived"] = p.archived
    audit(db, ctx.ws_id, ctx.principal, "version.update", "version", v.id, before, after)
    if recompute:
        recompute_version(db, ctx.ws, st, v)
    db.commit()
    return recompute


def delete_version(db: Session, ctx: WsCtx, version_id: int) -> None:
    ctx.require_user("admin")
    v, st = get_version(db, ctx, version_id)
    used = db.scalar(select(func.count()).select_from(PortfolioMember).where(PortfolioMember.version_id == v.id))
    if used:
        raise ApiError(409, "IN_USE", "version is pinned by a portfolio", hint="remove it from portfolios first")
    br = db.get(Branch, v.branch_id)
    if br.head_version_id == v.id:
        nxt = db.scalar(select(Version.id).where(Version.branch_id == br.id, Version.id != v.id)
                        .order_by(Version.seq.desc()).limit(1))
        set_head(db, ctx, br, nxt, "delete")
    audit(db, ctx.ws_id, ctx.principal, "version.delete", "version", v.id,
          {"strategy": st.slug, "branch": br.name, "label": v.label})
    _delete_points(db, select(Series.id).where(Series.version_id == v.id))
    db.delete(v)
    db.commit()


def delete_strategy(db: Session, ctx: WsCtx, slug: str) -> None:
    ctx.require_user("admin")
    st = get_strategy(db, ctx, slug)
    used = db.scalar(
        select(func.count()).select_from(PortfolioMember)
        .outerjoin(Version, Version.id == PortfolioMember.version_id)
        .outerjoin(Branch, Branch.id == PortfolioMember.branch_id)
        .where(or_(Version.strategy_id == st.id, Branch.strategy_id == st.id))
    )
    if used:
        raise ApiError(409, "IN_USE", "strategy is referenced by a portfolio")
    audit(db, ctx.ws_id, ctx.principal, "strategy.delete", "strategy", st.id, {"slug": st.slug})
    _delete_points(db, select(Series.id).join(Version, Version.id == Series.version_id)
                   .where(Version.strategy_id == st.id))
    st.default_branch_id = None
    db.execute(Branch.__table__.update().where(Branch.strategy_id == st.id)
               .values(head_version_id=None, forked_from_version_id=None))
    db.flush()
    db.delete(st)
    db.commit()


def _delete_points(db: Session, series_ids_select) -> None:
    ids = list(db.scalars(series_ids_select))
    if ids:
        db.execute(text("DELETE FROM series_points WHERE series_id = ANY(:ids)"), {"ids": ids})


def workspace_flags(db: Session, ctx: WsCtx, open_only: bool = True, limit: int = 500) -> list[dict]:
    q = (
        select(PointFlag, Series.name, Version.id, Version.label, Branch.name, Strategy.slug)
        .join(Series, Series.id == PointFlag.series_id)
        .join(Version, Version.id == Series.version_id)
        .join(Branch, Branch.id == Version.branch_id)
        .join(Strategy, Strategy.id == Version.strategy_id)
        .where(Strategy.workspace_id == ctx.ws_id)
    )
    if open_only:
        q = q.where(PointFlag.acked_at.is_(None))
    rows = db.execute(q.order_by(PointFlag.created_at.desc()).limit(limit)).all()
    return [{"strategy": s, "branch": b, "version": label, "version_id": vid, "series": sname,
             "date": _iso(f.d), "code": f.code, "value": f.value, "acked": f.acked_at is not None}
            for f, sname, vid, label, b, s in rows]


def check_new_slug(slug: str) -> str:
    return check_slug(slug, "slug")


def resolve_for_ui(db: Session, ctx: WsCtx, ref: str) -> dict:
    r = resolve(db, ctx.ws_id, ref)
    if r.kind == "portfolio":
        return {"kind": "portfolio", "slug": r.portfolio.slug}
    return {"kind": r.kind, "strategy": r.strategy.slug, "branch": r.branch.name,
            "version": r.version.label if r.version else None,
            "version_id": r.version.id if r.version else None}
