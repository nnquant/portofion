"""The single agent call: ensure strategy/branch/version exist, then push data."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from portofion_server.auth.deps import WsCtx
from portofion_server.db.models import (
    Branch,
    BranchHeadHistory,
    IngestBatch,
    PointFlag,
    Scalar,
    Strategy,
    StrategyTag,
    Tag,
    Version,
)
from portofion_server.domain.common import (
    SERIES_NAME_RE,
    TAG_COLORS,
    audit,
    check_label,
    check_slug,
    stable_hash,
    ws_settings,
)
from portofion_server.domain.metrics_cache import recompute_version
from portofion_server.domain.series_store import (
    get_or_create_series,
    latest_value_before,
    write_points,
)
from portofion_server.domain.validation import (
    Problems,
    check_returns,
    nav_to_returns,
    parse_date_list,
    parse_points,
)
from portofion_server.errors import ApiError
from portofion_server.schemas import BranchIn, IngestIn, ScalarIn, StrategyIn, VersionIn
from portofion_server.settings import get_settings


@dataclass
class IngestOutcome:
    response: dict[str, Any]
    changed_versions: list[int] = field(default_factory=list)
    moved_branches: list[int] = field(default_factory=list)


def _today() -> date:
    return datetime.now(UTC).date()


def version_fingerprint(params: dict | None, code_ref: str | None) -> str:
    return stable_hash({"params": params or {}, "code_ref": code_ref or None})


# --- ensure helpers ----------------------------------------------------------------------------


def pick_tag_color(used: Counter[str], avoid: set[str]) -> str:
    """Least-used palette color, skipping colors already on the same strategy when possible."""
    pool = [c for c in TAG_COLORS if c not in avoid] or list(TAG_COLORS)
    return min(pool, key=lambda c: (used[c], TAG_COLORS.index(c)))


def ensure_tags(db: Session, ws_id: int, strategy_id: int, names: list[str]) -> None:
    names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    if not names:
        return
    known = {r.name: r for r in db.execute(
        select(Tag.name, Tag.id, Tag.color).where(Tag.workspace_id == ws_id, Tag.name.in_(names)))}
    missing = [n for n in names if n not in known]
    if missing:
        used = Counter(db.scalars(select(Tag.color).where(Tag.workspace_id == ws_id, Tag.color.is_not(None))))
        avoid = {r.color for r in known.values() if r.color}
        avoid |= set(db.scalars(select(Tag.color).join(StrategyTag, StrategyTag.tag_id == Tag.id)
                                .where(StrategyTag.strategy_id == strategy_id, Tag.color.is_not(None))))
        for name in missing:
            color = pick_tag_color(used, avoid)
            db.execute(pg_insert(Tag).values(workspace_id=ws_id, name=name, color=color)
                       .on_conflict_do_nothing(index_elements=["workspace_id", "name"]))
            used[color] += 1
            avoid.add(color)
        known = {r.name: r for r in db.execute(
            select(Tag.name, Tag.id, Tag.color).where(Tag.workspace_id == ws_id, Tag.name.in_(names)))}
    for name in names:
        db.execute(pg_insert(StrategyTag).values(strategy_id=strategy_id, tag_id=known[name].id)
                   .on_conflict_do_nothing())


def ensure_strategy(db: Session, ctx: WsCtx, spec: str | StrategyIn) -> tuple[Strategy, bool]:
    s = spec if isinstance(spec, StrategyIn) else StrategyIn(slug=spec)
    check_slug(s.slug, "strategy slug")
    new_id = db.execute(
        pg_insert(Strategy)
        .values(workspace_id=ctx.ws_id, slug=s.slug, name=s.name or s.slug, kind=s.kind or "strategy",
                description=s.description or "", periods_per_year=s.periods_per_year)
        .on_conflict_do_nothing(index_elements=["workspace_id", "slug"])
        .returning(Strategy.id)
    ).scalar()
    st = db.scalar(select(Strategy).where(Strategy.workspace_id == ctx.ws_id, Strategy.slug == s.slug)
                   .execution_options(populate_existing=True))
    created = new_id is not None
    if created:
        audit(db, ctx.ws_id, ctx.principal, "strategy.create", "strategy", st.id, after={"slug": s.slug})
    else:
        changes = {}
        for f in ("name", "description", "kind", "periods_per_year"):
            v = getattr(s, f)
            if v is not None and getattr(st, f) != v:
                changes[f] = v
        if changes:
            before = {k: getattr(st, k) for k in changes}
            for k, v in changes.items():
                setattr(st, k, v)
            st.updated_at = func.now()
            audit(db, ctx.ws_id, ctx.principal, "strategy.update", "strategy", st.id, before, changes)
    if s.tags:
        ensure_tags(db, ctx.ws_id, st.id, s.tags)
    return st, created


def ensure_branch(db: Session, ctx: WsCtx, st: Strategy, spec: str | BranchIn | None) -> tuple[Branch, bool]:
    if spec is None:
        if st.default_branch_id:
            name = db.scalar(select(Branch.name).where(Branch.id == st.default_branch_id))
        else:
            name = "main"
        b = BranchIn(name=name)
    else:
        b = spec if isinstance(spec, BranchIn) else BranchIn(name=spec)
    check_slug(b.name, "branch name")
    forked = None
    if b.forked_from:
        from portofion_server.domain.refs import parse_ref, resolve_version_ref

        forked = resolve_version_ref(db, ctx.ws_id, parse_ref(b.forked_from)).id
    new_id = db.execute(
        pg_insert(Branch)
        .values(strategy_id=st.id, name=b.name, forked_from_version_id=forked)
        .on_conflict_do_nothing(index_elements=["strategy_id", "name"])
        .returning(Branch.id)
    ).scalar()
    # lock: serializes version creation on this branch
    br = db.scalar(select(Branch).where(Branch.strategy_id == st.id, Branch.name == b.name)
                   .with_for_update().execution_options(populate_existing=True))
    if st.default_branch_id is None:
        st.default_branch_id = br.id
    return br, new_id is not None


def set_head(db: Session, ctx: WsCtx | None, br: Branch, version_id: int | None, reason: str) -> None:
    if br.head_version_id == version_id:
        return
    br.head_version_id = version_id
    db.add(BranchHeadHistory(branch_id=br.id, version_id=version_id, reason=reason,
                             actor=ctx.principal.label if ctx else None))


def ensure_version(
    db: Session, ctx: WsCtx, st: Strategy, br: Branch, spec: str | VersionIn | None, warnings: list
) -> tuple[Version, bool, bool]:
    """Returns (version, created, head_moved)."""
    v = spec if isinstance(spec, VersionIn) else VersionIn(label=spec) if spec else VersionIn()
    if v.label is not None:
        check_label(v.label)
    fp = version_fingerprint(v.params, v.code_ref)
    existing: Version | None = None
    if v.label is not None:
        existing = db.scalar(select(Version).where(Version.branch_id == br.id, Version.label == v.label))
        if existing is not None:
            diff = {}
            if v.params is not None and v.params != (existing.params or {}):
                diff["params"] = {"stored": existing.params, "sent": v.params}
            if v.code_ref is not None and v.code_ref != existing.code_ref:
                diff["code_ref"] = {"stored": existing.code_ref, "sent": v.code_ref}
            if diff:
                raise ApiError(
                    409, "VERSION_CONFLICT",
                    f"version '{v.label}' already exists with different params/code_ref",
                    hint="versions are immutable: report under a new label, or omit params/code_ref",
                    details=diff,
                )
    elif v.params is not None or v.code_ref is not None:
        existing = db.scalar(select(Version).where(Version.branch_id == br.id, Version.fingerprint == fp)
                             .order_by(Version.seq.desc()).limit(1))
    elif br.head_version_id is not None:
        existing = db.get(Version, br.head_version_id)

    head_moved = False
    if existing is not None:
        ver, created = existing, False
        mutable = {}
        for f in ("stage", "live_since", "description", "meta", "code_repo"):
            val = getattr(v, f)
            if val is not None and getattr(ver, f) != val:
                mutable[f] = val
        if mutable:
            before = {k: getattr(ver, k) for k in mutable}
            for k, val in mutable.items():
                setattr(ver, k, val)
            audit(db, ctx.ws_id, ctx.principal, "version.update", "version", ver.id,
                  _jsonable(before), _jsonable(mutable))
        if v.make_head and br.head_version_id != ver.id:
            if br.head_locked:
                warnings.append({"code": "HEAD_LOCKED", "message": "branch head is locked; head not moved"})
            else:
                set_head(db, ctx, br, ver.id, "manual")
                head_moved = True
    else:
        seq = (db.scalar(select(func.max(Version.seq)).where(Version.branch_id == br.id)) or 0) + 1
        label = v.label or f"v{seq}"
        while db.scalar(select(Version.id).where(Version.branch_id == br.id, Version.label == label)):
            seq += 1
            label = f"v{seq}"
        ver = Version(
            branch_id=br.id, strategy_id=st.id, seq=seq, label=label, fingerprint=fp,
            params=v.params or {}, code_ref=v.code_ref, code_repo=v.code_repo,
            stage=v.stage or "research", live_since=v.live_since, description=v.description or "",
            meta=v.meta or {}, created_by_user_id=ctx.principal.user_id,
            created_by_key_id=ctx.principal.key_id,
        )
        db.add(ver)
        db.flush()
        created = True
        audit(db, ctx.ws_id, ctx.principal, "version.create", "version", ver.id,
              after={"strategy": st.slug, "branch": br.name, "label": label})
        if v.make_head is not False:
            if br.head_locked and br.head_version_id is not None:
                warnings.append({"code": "HEAD_LOCKED", "message": "branch head is locked; head not moved"})
            else:
                set_head(db, ctx, br, ver.id, "auto")
                head_moved = True
    return ver, created, head_moved


def _jsonable(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}


# --- the main entry ------------------------------------------------------------------------


def _count_points(raw: Any) -> int:
    if isinstance(raw, (list, dict)):
        return len(raw)
    return 0


def check_limits(body: IngestIn) -> None:
    cfg = get_settings()
    per_series = {"returns": _count_points(body.returns.points) if body.returns else 0}
    for name, s in body.series.items():
        per_series[name] = _count_points(s.points)
    if len(body.series) > cfg.max_series_per_item:
        raise ApiError(413, "BATCH_TOO_LARGE", f"more than {cfg.max_series_per_item} series in one item")
    for name, n in per_series.items():
        if n > cfg.max_points_per_series:
            raise ApiError(413, "BATCH_TOO_LARGE",
                           f"series '{name}' has {n} points (max {cfg.max_points_per_series})",
                           hint="split the backfill into chunks of <= 20000 points per call")
    if sum(per_series.values()) > cfg.max_points_per_request:
        raise ApiError(413, "BATCH_TOO_LARGE", "too many points in one request",
                       hint=f"max {cfg.max_points_per_request} points per request")


def ingest_item(
    db: Session,
    ctx: WsCtx,
    body: IngestIn,
    *,
    idempotency_key: str | None = None,
    request_sha: str | None = None,
) -> IngestOutcome:
    """Do all writes for one item (no commit). Raises ApiError on any hard error."""
    check_limits(body)
    s = ws_settings(ctx.ws)
    probs = Problems()

    # ---- parse everything before touching the database ----
    ret_points: list[tuple[date, float]] = []
    ret_delete: list[date] = []
    nav_points: list[tuple[date, float]] = []
    if body.returns is not None:
        raw = parse_points(body.returns.points, "returns.points", probs)
        ret_delete = parse_date_list(body.returns.delete, "returns.delete", probs)
        if body.returns.kind == "nav":
            for i, (d, v) in enumerate(raw):
                if v <= 0:
                    probs.error(f"returns.points[{i}]", "NAV_NOT_POSITIVE", f"NAV {v} must be > 0", d.isoformat())
            nav_points = raw
        elif body.returns.kind == "log_return":
            ret_points = [(d, math.expm1(v)) for d, v in raw]
        else:
            ret_points = raw
    extras: dict[str, tuple[list, list]] = {}
    for name, ser in body.series.items():
        if not SERIES_NAME_RE.match(name) or name == "returns":
            probs.error(f"series.{name}", "BAD_SERIES_NAME",
                        "series names are lowercase [a-z][a-z0-9_]*; 'returns' is reserved")
            continue
        if name == "nav" and body.returns is not None and body.returns.kind == "nav":
            probs.error("series.nav", "BAD_SERIES_NAME", "'nav' is filled automatically when returns.kind='nav'")
            continue
        extras[name] = (parse_points(ser.points, f"series.{name}.points", probs),
                        parse_date_list(ser.delete, f"series.{name}.delete", probs))
    scalars: dict[str, ScalarIn] = {}
    for name, val in body.scalars.items():
        sc = val if isinstance(val, ScalarIn) else ScalarIn(value=val)
        if not SERIES_NAME_RE.match(name):
            probs.error(f"scalars.{name}", "BAD_SCALAR_NAME", "scalar names are lowercase [a-z][a-z0-9_]*")
        elif not math.isfinite(sc.value):
            probs.error(f"scalars.{name}", "NON_FINITE", "scalar value must be finite")
        else:
            scalars[name] = sc
    if probs.errors:
        raise ApiError(422, "VALIDATION_ERROR", f"{len(probs.errors)} invalid field(s); nothing was written",
                       details=probs.errors)

    # ---- ensure catalog objects ----
    warnings: list[dict] = []
    st, st_created = ensure_strategy(db, ctx, body.strategy)
    br, br_created = ensure_branch(db, ctx, st, body.branch)
    ver, ver_created, head_moved = ensure_version(db, ctx, st, br, body.version, warnings)

    # ---- nav conversion needs the stored anchor ----
    anchor_dropped = None
    nav_series = None
    if nav_points:
        nav_series = get_or_create_series(db, ver.id, "nav", "last")
        base = body.returns.base_nav
        if base is None:
            base = latest_value_before(db, nav_series.id, nav_points[0][0])
        ret_points, anchor_dropped = nav_to_returns(nav_points, base, "returns.points", probs)

    flags = []
    if ret_points:
        flags = check_returns(ret_points, "returns.points", probs, float(s["outlier_abs"]), body.strict)
    if probs.errors:
        raise ApiError(422, "VALIDATION_ERROR", f"{len(probs.errors)} invalid value(s); nothing was written",
                       details=probs.errors)
    warnings.extend(probs.warnings)

    # ---- batch record ----
    batch = IngestBatch(
        workspace_id=ctx.ws_id, api_key_id=ctx.principal.key_id, user_id=ctx.principal.user_id,
        version_id=ver.id, idempotency_key=idempotency_key, request_sha256=request_sha,
        client=body.client or {},
    )
    db.add(batch)
    db.flush()

    today = _today()
    series_out: dict[str, dict] = {}
    returns_changed = False
    if body.returns is not None:
        rs = get_or_create_series(db, ver.id, "returns", "compound")
        stats = write_points(db, rs, ret_points, replace=body.returns.replace, delete=ret_delete,
                             on_existing=body.on_existing, batch_id=batch.id, today=today)
        returns_changed = stats.pop("changed")
        if anchor_dropped is not None:
            stats["anchor_dropped"] = anchor_dropped.isoformat()
        series_out["returns"] = stats
        _write_flags(db, rs.id, ret_points, flags, batch.id)
        if nav_series is not None:
            nst = write_points(db, nav_series, nav_points, replace=body.returns.replace, delete=ret_delete,
                               on_existing="update", batch_id=batch.id, today=today)
            nst.pop("changed")
            series_out["nav"] = nst
    for name, (pts, dels) in extras.items():
        ser = body.series[name]
        es = get_or_create_series(db, ver.id, name, ser.agg, ser.unit)
        est = write_points(db, es, pts, replace=ser.replace, delete=dels, on_existing=body.on_existing,
                           batch_id=batch.id, today=today)
        est.pop("changed")
        series_out[name] = est

    if scalars:
        rows = [{"version_id": ver.id, "name": k, "value": v.value, "as_of": v.as_of, "batch_id": batch.id}
                for k, v in scalars.items()]
        stmt = pg_insert(Scalar).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["version_id", "name"],
            set_={"value": stmt.excluded.value, "as_of": stmt.excluded.as_of,
                  "batch_id": stmt.excluded.batch_id, "updated_at": func.now()},
        )
        db.execute(stmt)

    db.flush()
    m = recompute_version(db, ctx.ws, st, ver)
    response = {
        "ok": True,
        "batch_id": batch.id,
        "replayed": False,
        "dry_run": body.dry_run,
        "strategy": {"slug": st.slug, "created": st_created},
        "branch": {"name": br.name, "created": br_created},
        "version": {"label": ver.label, "id": ver.id, "created": ver_created,
                    "is_head": br.head_version_id == ver.id},
        "series": series_out,
        "scalars": {"set": sorted(scalars)},
        "warnings": warnings,
        "metrics": {k: m.get(k) for k in ("n", "first_date", "last_date", "sharpe", "ann_ret", "ann_vol", "max_dd")},
        "url": f"/w/{ctx.ws.slug}/s/{st.slug}?b={br.name}&v={ver.label}",  # made absolute by the API layer
    }
    batch.response = response
    changed = [ver.id] if (returns_changed or ver_created) else []
    return IngestOutcome(response, changed, [br.id] if head_moved else [])


def _write_flags(db: Session, series_id: int, points, flags: list[dict], batch_id: int) -> None:
    if points:
        outlier_dates = [f["d"] for f in flags if f["code"] == "OUTLIER"]
        batch_dates = [d for d, _ in points]
        q = delete(PointFlag).where(
            PointFlag.series_id == series_id, PointFlag.code == "OUTLIER", PointFlag.d.in_(batch_dates)
        )
        if outlier_dates:
            q = q.where(PointFlag.d.not_in(outlier_dates))
        db.execute(q)
    if not flags:
        return
    rows = [{"series_id": series_id, "d": f["d"], "code": f["code"], "value": f["value"], "batch_id": batch_id}
            for f in flags]
    stmt = pg_insert(PointFlag).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["series_id", "d", "code"],
        set_={
            "value": stmt.excluded.value,
            "batch_id": stmt.excluded.batch_id,
            # a changed value re-opens an acknowledged flag
            "acked_at": case(
                (PointFlag.value.is_distinct_from(stmt.excluded.value), None),
                else_=PointFlag.acked_at,
            ),
        },
    )
    db.execute(stmt)


def find_replay(db: Session, ws_id: int, key: str, request_sha: str) -> dict | None:
    prior = db.scalar(select(IngestBatch).where(IngestBatch.workspace_id == ws_id,
                                                IngestBatch.idempotency_key == key))
    if prior is None:
        return None
    if prior.request_sha256 != request_sha:
        raise ApiError(409, "IDEMPOTENCY_MISMATCH",
                       "Idempotency-Key was already used with a different request body",
                       hint="use a fresh key for a different payload")
    if prior.response is None:
        raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "a request with this key is still in progress")
    return {**prior.response, "replayed": True}
