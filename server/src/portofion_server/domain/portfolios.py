"""Portfolio persistence, nested resolution, fingerprinted result cache, invalidation."""

from __future__ import annotations

import contextlib
import json
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from portofion_engine import ENGINE_VERSION
from portofion_engine.analytics import _dates, _vals
from portofion_engine.drawdown import drawdown_series, top_drawdowns
from portofion_engine.metrics import summary, trailing_returns
from portofion_engine.portfolio import (
    CycleError,
    DepthError,
    Member,
    PortfolioNode,
    PortfolioResult,
    PortfolioSpec,
    evaluate,
    find_cycle,
)
from portofion_engine.sparkline import sparkline
from portofion_engine.tables import monthly_table
from portofion_server.auth.deps import WsCtx
from portofion_server.db.models import (
    Branch,
    Portfolio,
    PortfolioMember,
    PortfolioResultRow,
    PortfolioWeight,
    Series,
    Strategy,
    Version,
    Workspace,
)
from portofion_server.domain.common import audit, check_slug, periods_for, stable_hash, ws_settings
from portofion_server.domain.refs import Resolved, parse_ref, resolve
from portofion_server.domain.series_store import load_series
from portofion_server.errors import ApiError, not_found
from portofion_server.schemas import MemberIn, PortfolioIn, PortfolioSpecIn

MAX_PLOT_POINTS = 1500
LOCK_NS = 7001


# --- spec ---------------------------------------------------------------------------------------


def engine_spec(spec: dict, ws: Workspace) -> PortfolioSpec:
    s = PortfolioSpecIn.model_validate(spec)
    stale = s.stale_days if s.stale_days is not None else int(ws_settings(ws)["stale_days"])
    return PortfolioSpec(
        scheme=s.scheme, rebalance=s.rebalance, lookback=s.lookback, min_obs=s.min_obs, start=s.start,
        cost_bps=s.cost_bps, gross=s.gross, renormalize=s.renormalize, stale_days=stale,
        shrinkage=s.shrinkage,
    )


# --- member resolution --------------------------------------------------------------------------


@dataclass
class ResolvedMember:
    key: str
    ref_kind: str
    version_id: int | None = None
    branch_id: int | None = None
    child_portfolio_id: int | None = None
    weight: float | None = None


def resolve_members(db: Session, ws_id: int, members: list[MemberIn]) -> list[ResolvedMember]:
    out: list[ResolvedMember] = []
    seen: set[str] = set()
    errors = []
    for i, m in enumerate(members):
        try:
            r: Resolved = resolve(db, ws_id, parse_ref(m.ref))
        except ApiError as e:
            errors.append({"loc": f"members[{i}].ref", "code": e.code, "message": e.message})
            continue
        if r.kind == "portfolio":
            rm = ResolvedMember(r.canonical, "portfolio", child_portfolio_id=r.portfolio.id, weight=m.weight)
        elif r.kind == "branch":
            rm = ResolvedMember(r.canonical, "branch", branch_id=r.branch.id, weight=m.weight)
        else:
            rm = ResolvedMember(r.canonical, "version", version_id=r.version.id, weight=m.weight)
        if rm.key in seen:
            errors.append({"loc": f"members[{i}].ref", "code": "DUPLICATE_MEMBER",
                           "message": f"{rm.key} listed twice"})
            continue
        seen.add(rm.key)
        out.append(rm)
    if errors:
        raise ApiError(422, "VALIDATION_ERROR", "invalid portfolio members", details=errors)
    return out


def _edges(db: Session, ws_id: int) -> dict[str, list[str]]:
    rows = db.execute(
        select(PortfolioMember.portfolio_id, PortfolioMember.child_portfolio_id)
        .join(Portfolio, Portfolio.id == PortfolioMember.portfolio_id)
        .where(Portfolio.workspace_id == ws_id, PortfolioMember.child_portfolio_id.is_not(None))
    ).all()
    edges: dict[str, list[str]] = {}
    for p, c in rows:
        edges.setdefault(f"pf:{p}", []).append(f"pf:{c}")
    return edges


def check_cycle(db: Session, ws_id: int, pid: int | None, members: list[ResolvedMember]) -> None:
    edges = _edges(db, ws_id)
    me = f"pf:{pid if pid is not None else 'new'}"
    edges[me] = [f"pf:{m.child_portfolio_id}" for m in members if m.child_portfolio_id]
    cyc = find_cycle(edges, me)
    if cyc:
        names = dict(db.execute(select(Portfolio.id, Portfolio.slug).where(Portfolio.workspace_id == ws_id)).all())
        path = [f"pf:{names.get(int(n[3:]), 'new')}" if n[3:].isdigit() else "pf:(this)" for n in cyc]
        raise ApiError(422, "PORTFOLIO_CYCLE", "portfolio would contain itself: " + " -> ".join(path),
                       details={"path": path})


def _validate_weights(spec: PortfolioSpecIn, members: list[ResolvedMember]) -> None:
    if spec.scheme == "fixed" and members:
        if all(m.weight is None for m in members):
            for m in members:
                m.weight = 1.0 / len(members)
        if abs(sum(m.weight or 0.0 for m in members)) < 1e-12:
            raise ApiError(422, "VALIDATION_ERROR", "fixed weights sum to zero")


def get_portfolio(db: Session, ctx: WsCtx, slug: str) -> Portfolio:
    pf = db.scalar(select(Portfolio).where(Portfolio.workspace_id == ctx.ws_id, Portfolio.slug == slug))
    if pf is None:
        raise not_found("portfolio")
    return pf


def save_portfolio(db: Session, ctx: WsCtx, body: PortfolioIn, existing_slug: str | None = None) -> Portfolio:
    ctx.require_user("member")
    check_slug(body.slug, "portfolio slug")
    members = resolve_members(db, ctx.ws_id, body.members)
    _validate_weights(body.spec, members)
    if existing_slug is None:
        if db.scalar(select(Portfolio.id).where(Portfolio.workspace_id == ctx.ws_id, Portfolio.slug == body.slug)):
            raise ApiError(409, "CONFLICT", f"portfolio {body.slug!r} already exists")
        check_cycle(db, ctx.ws_id, None, members)
        pf = Portfolio(workspace_id=ctx.ws_id, slug=body.slug, name=body.name or body.slug,
                       description=body.description, spec=body.spec.model_dump(mode="json"),
                       created_by=ctx.principal.user_id)
        db.add(pf)
        db.flush()
        audit(db, ctx.ws_id, ctx.principal, "portfolio.create", "portfolio", pf.id, after={"slug": pf.slug})
    else:
        pf = get_portfolio(db, ctx, existing_slug)
        if body.slug != pf.slug and db.scalar(
            select(Portfolio.id).where(Portfolio.workspace_id == ctx.ws_id, Portfolio.slug == body.slug)
        ):
            raise ApiError(409, "CONFLICT", f"portfolio {body.slug!r} already exists")
        check_cycle(db, ctx.ws_id, pf.id, members)
        before = {"spec": pf.spec, "slug": pf.slug}
        pf.slug = body.slug
        pf.name = body.name or pf.name
        pf.description = body.description
        pf.spec = body.spec.model_dump(mode="json")
        pf.spec_rev += 1
        pf.updated_at = func.now()
        audit(db, ctx.ws_id, ctx.principal, "portfolio.update", "portfolio", pf.id, before, {"spec": pf.spec})
    _sync_members(db, pf, members)
    db.flush()
    mark_stale(db, dependents(db, portfolio_ids=[pf.id]))
    db.commit()
    return pf


def _sync_members(db: Session, pf: Portfolio, members: list[ResolvedMember]) -> None:
    current = {m.key: m for m in db.scalars(select(PortfolioMember).where(PortfolioMember.portfolio_id == pf.id))}
    keep = set()
    for pos, m in enumerate(members):
        row = current.get(m.key)
        if row is None:
            row = PortfolioMember(portfolio_id=pf.id, key=m.key)
            db.add(row)
        row.position = pos
        row.ref_kind = m.ref_kind
        row.version_id, row.branch_id, row.child_portfolio_id = m.version_id, m.branch_id, m.child_portfolio_id
        row.weight = m.weight
        keep.add(m.key)
    gone = [r.id for k, r in current.items() if k not in keep]
    if gone:
        db.execute(delete(PortfolioMember).where(PortfolioMember.id.in_(gone)))


def delete_portfolio(db: Session, ctx: WsCtx, slug: str) -> None:
    ctx.require_user("member")
    pf = get_portfolio(db, ctx, slug)
    parents = db.scalars(select(Portfolio.slug).join(PortfolioMember, PortfolioMember.portfolio_id == Portfolio.id)
                         .where(PortfolioMember.child_portfolio_id == pf.id)).all()
    if parents:
        raise ApiError(409, "IN_USE", f"portfolio is a member of {', '.join(parents)}")
    audit(db, ctx.ws_id, ctx.principal, "portfolio.delete", "portfolio", pf.id, {"slug": pf.slug})
    db.delete(pf)
    db.commit()


# --- reported weights -----------------------------------------------------------------------


def report_weights(db: Session, ctx: WsCtx, slug: str, reports: list, *, auto_add: bool, replace: bool) -> dict:
    from portofion_server.domain.validation import parse_date

    pf = get_portfolio(db, ctx, slug)
    members = {m.key: m for m in db.scalars(select(PortfolioMember).where(PortfolioMember.portfolio_id == pf.id))}
    errors = []
    rows = []
    for i, rep in enumerate(reports):
        d = parse_date(rep.date)
        if d is None:
            errors.append({"loc": f"reports[{i}].date", "code": "BAD_DATE", "message": "date must be YYYY-MM-DD"})
            continue
        for ref, w in rep.weights.items():
            if not np.isfinite(w):
                errors.append({"loc": f"reports[{i}].weights.{ref}", "code": "NON_FINITE", "message": "weight must be finite"})
                continue
            try:
                key = resolve(db, ctx.ws_id, ref)
            except ApiError as e:
                errors.append({"loc": f"reports[{i}].weights.{ref}", "code": e.code, "message": e.message})
                continue
            canon = key.canonical
            m = members.get(canon)
            if m is None:
                if not auto_add:
                    errors.append({"loc": f"reports[{i}].weights.{ref}", "code": "NOT_A_MEMBER",
                                   "message": f"{canon} is not a member", })
                    continue
                rm = resolve_members(db, ctx.ws_id, [MemberIn(ref=ref)])[0]
                if rm.child_portfolio_id:
                    check_cycle(db, ctx.ws_id, pf.id, [rm])
                m = PortfolioMember(portfolio_id=pf.id, key=rm.key, position=len(members), ref_kind=rm.ref_kind,
                                    version_id=rm.version_id, branch_id=rm.branch_id,
                                    child_portfolio_id=rm.child_portfolio_id)
                db.add(m)
                db.flush()
                members[canon] = m
            rows.append({"portfolio_id": pf.id, "d": d, "member_id": m.id, "weight": float(w)})
    if errors:
        raise ApiError(422, "VALIDATION_ERROR", "invalid weight report; nothing was written", details=errors[:100])
    if replace:
        db.execute(delete(PortfolioWeight).where(PortfolioWeight.portfolio_id == pf.id))
    else:
        dates = sorted({r["d"] for r in rows})
        if dates:
            # a report for a date replaces that date's full weight vector
            db.execute(delete(PortfolioWeight).where(PortfolioWeight.portfolio_id == pf.id,
                                                     PortfolioWeight.d.in_(dates)))
    if rows:
        db.execute(pg_insert(PortfolioWeight).values(rows))
    pf.weights_rev += 1
    mark_stale(db, dependents(db, portfolio_ids=[pf.id]))
    db.commit()
    return {"ok": True, "portfolio": pf.slug, "dates": len({r["d"] for r in rows}), "rows": len(rows),
            "weights_rev": pf.weights_rev}


def _reported_frame(db: Session, pid: int, keys_by_member: dict[int, str]) -> pd.DataFrame | None:
    rows = db.execute(select(PortfolioWeight.d, PortfolioWeight.member_id, PortfolioWeight.weight)
                      .where(PortfolioWeight.portfolio_id == pid)).all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["d", "m", "w"])
    df["k"] = df["m"].map(keys_by_member)
    out = df.pivot_table(index="d", columns="k", values="w", aggfunc="last").fillna(0.0)
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index))
    return out


# --- structure, fingerprint, evaluation -------------------------------------------------------


@dataclass
class Structure:
    nodes: dict[int, dict] = field(default_factory=dict)  # pid -> {pf, members: [...], fp}
    leaves: dict[int, dict] = field(default_factory=dict)  # version_id -> {series_id, data_rev, ppy, ...}


def _load_structure(db: Session, ws: Workspace, root_ids: list[int],
                    extra_root: dict | None = None) -> Structure:
    st = Structure()
    pending = list(root_ids)
    if extra_root is not None:
        pending += [m.child_portfolio_id for m in extra_root["members"] if m.child_portfolio_id]
    seen: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pf = db.get(Portfolio, pid)
        if pf is None or pf.workspace_id != ws.id:
            continue
        mem = db.scalars(select(PortfolioMember).where(PortfolioMember.portfolio_id == pid)
                         .order_by(PortfolioMember.position)).all()
        st.nodes[pid] = {"pf": pf, "members": mem}
        pending += [m.child_portfolio_id for m in mem if m.child_portfolio_id]
    # resolve branch heads -> versions
    all_members = [m for n in st.nodes.values() for m in n["members"]]
    if extra_root is not None:
        all_members += extra_root["members"]
    bids = {m.branch_id for m in all_members if m.branch_id}
    heads = dict(db.execute(select(Branch.id, Branch.head_version_id).where(Branch.id.in_(bids))).all()) if bids else {}
    vids = {m.version_id for m in all_members if m.version_id} | {v for v in heads.values() if v}
    if vids:
        rows = db.execute(
            select(Version.id, Series.id, Series.data_rev, Strategy, Version.label, Branch.name)
            .join(Strategy, Strategy.id == Version.strategy_id)
            .join(Branch, Branch.id == Version.branch_id)
            .outerjoin(Series, (Series.version_id == Version.id) & (Series.name == "returns"))
            .where(Version.id.in_(vids))
        ).all()
        for vid, sid, rev, strat, label, bname in rows:
            st.leaves[vid] = {"series_id": sid, "data_rev": rev or 0, "ppy": periods_for(ws, strat),
                              "ref": f"{strat.slug}/{bname}@{label}"}
    st.heads = heads  # type: ignore[attr-defined]
    return st


def _member_leaf(st: Structure, m) -> int | None:
    if m.version_id:
        return m.version_id
    if m.branch_id:
        return st.heads.get(m.branch_id)  # type: ignore[attr-defined]
    return None


def _fingerprints(st: Structure, ws: Workspace, extra_root: dict | None = None) -> dict:
    base = [ENGINE_VERSION, ws_settings(ws)["stale_days"]]
    memo: dict[int, str] = {}

    def fp(pid: int, stack: tuple = ()) -> str:
        if pid in memo:
            return memo[pid]
        if pid in stack:
            raise ApiError(422, "PORTFOLIO_CYCLE", "portfolio cycle detected")
        n = st.nodes[pid]
        memo[pid] = _fp_members(n["pf"].spec, n["pf"].spec_rev, n["pf"].weights_rev, n["members"], (*stack, pid))
        return memo[pid]

    def _fp_members(spec, spec_rev, weights_rev, members, stack) -> str:
        parts = []
        for m in members:
            if m.child_portfolio_id:
                child = fp(m.child_portfolio_id, stack) if m.child_portfolio_id in st.nodes else "missing"
                parts.append([m.key, "pf", child, m.weight])
            else:
                vid = _member_leaf(st, m)
                leaf = st.leaves.get(vid) if vid else None
                parts.append([m.key, vid, leaf["data_rev"] if leaf else None, leaf["ppy"] if leaf else None, m.weight])
        return stable_hash([base, spec, spec_rev, weights_rev, parts])

    for pid in st.nodes:
        fp(pid)
    if extra_root is not None:
        memo[-1] = _fp_members(extra_root["spec"], 0, 0, extra_root["members"], ())
    return memo


def _evaluate(db: Session, ws: Workspace, st: Structure, root: int,
              extra_root: dict | None = None) -> tuple[dict[int, PortfolioResult], dict]:
    leaves_series = load_series(db, [(v["series_id"], v["data_rev"]) for v in st.leaves.values() if v["series_id"]])
    empty = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    leaves = {
        str(vid): (leaves_series.get(v["series_id"], empty) if v["series_id"] else empty, v["ppy"])
        for vid, v in st.leaves.items()
    }
    nodes: dict[str, PortfolioNode] = {}

    def node_from(pid: str, spec: dict, members, reported) -> PortfolioNode:
        mem = []
        weights = {}
        for m in members:
            if m.child_portfolio_id:
                mem.append(Member(m.key, f"pf:{m.child_portfolio_id}"))
            else:
                vid = _member_leaf(st, m)
                mem.append(Member(m.key, f"leaf:{vid}" if vid else "leaf:none"))
            if m.weight is not None:
                weights[m.key] = m.weight
        return PortfolioNode(pid, engine_spec(spec, ws), mem, weights, reported)

    for pid, n in st.nodes.items():
        keys = {m.id: m.key for m in n["members"]}
        rep = _reported_frame(db, pid, keys) if n["pf"].spec.get("scheme") == "reported" else None
        nodes[str(pid)] = node_from(str(pid), n["pf"].spec, n["members"], rep)
    if extra_root is not None:
        nodes["-1"] = node_from("-1", extra_root["spec"], extra_root["members"], extra_root.get("reported"))
    try:
        res = evaluate(str(root), nodes, leaves)
    except CycleError as e:
        raise ApiError(422, "PORTFOLIO_CYCLE", str(e)) from e
    except DepthError as e:
        raise ApiError(422, "PORTFOLIO_TOO_DEEP", str(e)) from e
    return {int(k): v for k, v in res.items()}, leaves


def _subsample(n: int, keep: list[int]) -> np.ndarray:
    if n <= MAX_PLOT_POINTS:
        return np.arange(n)
    pos = np.unique(np.concatenate([np.linspace(0, n - 1, MAX_PLOT_POINTS).astype(int), np.array(keep, dtype=int)]))
    return pos


def _member_series(m, st: Structure, leaves: dict, results: dict) -> pd.Series | None:
    if m.child_portfolio_id:
        child = results.get(m.child_portfolio_id)
        return child.returns if child is not None else None
    vid = _member_leaf(st, m)
    return leaves.get(str(vid), (None, None))[0] if vid else None


def build_payload(res: PortfolioResult, members, st: Structure, ws: Workspace, spec: dict,
                  child_slugs: dict[int, str], leaves: dict | None = None,
                  results: dict | None = None) -> dict:
    r = res.returns
    ppy = res.periods_per_year
    s = ws_settings(ws)
    nav = (1.0 + r).cumprod()
    m_info = []
    idx = r.index
    rebal_pos = [int(idx.get_loc(d)) for d in res.rebalances] if len(idx) else []
    sub = _subsample(len(idx), rebal_pos + ([len(idx) - 1] if len(idx) else []))
    member_nav = {}
    for m in members:
        if m.child_portfolio_id:
            label = f"pf:{child_slugs.get(m.child_portfolio_id, m.child_portfolio_id)}"
            resolved = None
        else:
            vid = _member_leaf(st, m)
            resolved = st.leaves.get(vid, {}).get("ref") if vid else None
            label = m.key
        ms = _member_series(m, st, leaves or {}, results or {})
        if ms is not None and len(idx):
            w = ms[(ms.index >= idx[0]) & (ms.index <= idx[-1])]
            mnav = (1.0 + w).cumprod().reindex(idx[sub], method="ffill")
            member_nav[m.key] = _vals(mnav.fillna(1.0) if len(w) and w.index[0] <= idx[0] else mnav, 6)
        cur_w = float(res.weights[m.key].iloc[-1]) if len(idx) and m.key in res.weights else None
        contrib = float(res.contributions[m.key].sum()) if len(idx) and m.key in res.contributions else None
        m_info.append({
            "key": m.key, "ref_kind": m.ref_kind, "label": label, "resolved": resolved,
            "weight": m.weight, "current_weight": cur_w, "contribution": contrib,
            "risk_contrib": (res.risk_contrib or {}).get(m.key),
            "lagging": m.key in res.lagging, "excluded": m.key in res.excluded,
        })
    if len(idx):
        w_sub = res.weights.iloc[sub]
        weights = {"dates": _dates(w_sub.index), "series": {c: _vals(w_sub[c], 6) for c in w_sub.columns}}
    else:
        weights = {"dates": [], "series": {}}
    return {
        "spec": spec,
        "start": res.start.date().isoformat() if res.start is not None else None,
        "asof": res.asof.date().isoformat() if res.asof is not None else None,
        "periods_per_year": ppy,
        "dates": _dates(idx),
        "returns": _vals(r, 10),
        "nav": _vals(nav),
        "drawdown": _vals(drawdown_series(r)),
        "metrics": summary(r, ppy, s["rf"], s["var_level"]),
        "trailing": trailing_returns(r),
        "monthly": monthly_table(r),
        "top_dd": [e.to_dict() for e in top_drawdowns(r, 5)],
        "members": m_info,
        "member_nav": {"dates": _dates(idx[sub]) if len(idx) else [], "series": member_nav},
        "weights": weights,
        "rebalances": _dates(pd.DatetimeIndex(res.rebalances)),
        "turnover": _vals(res.turnover, 6),
        "warnings": res.warnings,
        "lagging": res.lagging,
    }


def _store(db: Session, pid: int, fp: str, payload: dict | None, error: str | None) -> None:
    if payload is not None:
        r = pd.Series(payload["returns"], index=pd.DatetimeIndex(payload["dates"]), dtype="float64")
        values = {"fingerprint": fp, "stale": False, "status": "ok", "error": None,
                  "asof": date.fromisoformat(payload["asof"]) if payload["asof"] else None,
                  "metrics": {**payload["metrics"], **payload["trailing"]},
                  "spark": sparkline(r.dropna(), 64),
                  "payload": zlib.compress(json.dumps(payload).encode(), 6)}
    else:
        values = {"fingerprint": fp, "stale": False, "status": "error", "error": error, "asof": None,
                  "metrics": {}, "spark": [], "payload": b""}
    stmt = pg_insert(PortfolioResultRow).values(portfolio_id=pid, **values, computed_at=func.now())
    stmt = stmt.on_conflict_do_update(index_elements=["portfolio_id"],
                                      set_={**{k: stmt.excluded[k] for k in values}, "computed_at": func.now()})
    db.execute(stmt)


def _child_slugs(db: Session, st: Structure) -> dict[int, str]:
    return {pid: n["pf"].slug for pid, n in st.nodes.items()}


def compute_result(db: Session, ws: Workspace, pf: Portfolio, *, force: bool = False) -> dict:
    """Return the cached payload when the fingerprint matches, else compute and store."""
    st = _load_structure(db, ws, [pf.id])
    fps = _fingerprints(st, ws)
    fp = fps[pf.id]
    if not force:
        row = db.get(PortfolioResultRow, pf.id)
        if row is not None and row.fingerprint == fp and row.status == "ok":
            return _decode(row)
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :id)"), {"ns": LOCK_NS, "id": pf.id})
    db.expire_all()
    row = db.get(PortfolioResultRow, pf.id)
    if not force and row is not None and row.fingerprint == fp and row.status == "ok":
        db.commit()
        return _decode(row)
    try:
        results, leaves = _evaluate(db, ws, st, pf.id)
    except ApiError as e:
        _store(db, pf.id, fp, None, e.message)
        db.commit()
        raise
    slugs = _child_slugs(db, st)
    out = None
    for pid, res in results.items():
        node = st.nodes[pid]
        payload = build_payload(res, node["members"], st, ws, node["pf"].spec, slugs, leaves, results)
        if pid == pf.id:
            out = payload
            _store(db, pid, fp, payload, None)
        else:
            child = db.get(PortfolioResultRow, pid)
            if child is None or child.fingerprint != fps[pid] or child.stale:
                _store(db, pid, fps[pid], payload, None)
    db.commit()
    return {**out, "computed_at": datetime.now().isoformat(), "fingerprint": fp, "cached": False}


def _decode(row: PortfolioResultRow) -> dict:
    payload = json.loads(zlib.decompress(row.payload))
    return {**payload, "computed_at": row.computed_at.isoformat(), "fingerprint": row.fingerprint, "cached": True}


def preview(db: Session, ctx: WsCtx, spec: PortfolioSpecIn, members_in: list[MemberIn], slug: str | None) -> dict:
    members = resolve_members(db, ctx.ws_id, members_in)
    _validate_weights(spec, members)
    pid = None
    reported = None
    if slug:
        pf = get_portfolio(db, ctx, slug)
        pid = pf.id
        current = {m.id: m.key for m in db.scalars(select(PortfolioMember).where(PortfolioMember.portfolio_id == pid))}
        if spec.scheme == "reported":
            reported = _reported_frame(db, pid, current)
    check_cycle(db, ctx.ws_id, pid, members)
    if not members:
        raise ApiError(422, "VALIDATION_ERROR", "a portfolio needs at least one member")
    spec_json = spec.model_dump(mode="json")
    extra = {"spec": spec_json, "members": members, "reported": reported}
    st = _load_structure(db, ctx.ws, [], extra_root=extra)
    results, leaves = _evaluate(db, ctx.ws, st, -1, extra_root=extra)
    return build_payload(results[-1], members, st, ctx.ws, spec_json, _child_slugs(db, st), leaves, results)


def list_portfolios(db: Session, ctx: WsCtx) -> list[dict]:
    rows = db.execute(
        select(Portfolio, PortfolioResultRow)
        .outerjoin(PortfolioResultRow, PortfolioResultRow.portfolio_id == Portfolio.id)
        .where(Portfolio.workspace_id == ctx.ws_id).order_by(Portfolio.slug)
    ).all()
    counts = dict(db.execute(
        select(PortfolioMember.portfolio_id, func.count()).group_by(PortfolioMember.portfolio_id)
    ).all())
    out = []
    for pf, res in rows:
        out.append({
            "slug": pf.slug, "name": pf.name, "description": pf.description,
            "scheme": pf.spec.get("scheme"), "rebalance": pf.spec.get("rebalance"),
            "n_members": counts.get(pf.id, 0), "updated_at": pf.updated_at.isoformat(),
            "archived": pf.archived_at is not None,
            "status": res.status if res else None, "stale": bool(res.stale) if res else True,
            "asof": res.asof.isoformat() if res and res.asof else None,
            "metrics": res.metrics if res else {}, "spark": list(res.spark) if res and res.spark else [],
        })
    return out


def portfolio_detail(db: Session, ctx: WsCtx, slug: str) -> dict:
    pf = get_portfolio(db, ctx, slug)
    mem = db.scalars(select(PortfolioMember).where(PortfolioMember.portfolio_id == pf.id)
                     .order_by(PortfolioMember.position)).all()
    n_weights = db.scalar(select(func.count(func.distinct(PortfolioWeight.d))).where(PortfolioWeight.portfolio_id == pf.id))
    parents = db.scalars(select(Portfolio.slug).join(PortfolioMember, PortfolioMember.portfolio_id == Portfolio.id)
                         .where(PortfolioMember.child_portfolio_id == pf.id)).all()
    child_slugs = dict(db.execute(select(Portfolio.id, Portfolio.slug).where(
        Portfolio.id.in_([m.child_portfolio_id for m in mem if m.child_portfolio_id]))).all())
    return {
        "slug": pf.slug, "name": pf.name, "description": pf.description, "spec": pf.spec,
        "spec_rev": pf.spec_rev, "weights_rev": pf.weights_rev, "n_weight_dates": n_weights or 0,
        "parents": parents,
        "members": [{"key": m.key if not m.child_portfolio_id else f"pf:{child_slugs.get(m.child_portfolio_id)}",
                     "ref_kind": m.ref_kind, "weight": m.weight} for m in mem],
        "updated_at": pf.updated_at.isoformat(),
    }


# --- invalidation ----------------------------------------------------------------------------


def dependents(db: Session, *, version_ids=(), branch_ids=(), portfolio_ids=()) -> list[int]:
    """Portfolios affected by changes to versions/branch heads/portfolios, closed upward."""
    vids, bids, pids = list(version_ids), list(branch_ids), list(portfolio_ids)
    if not (vids or bids or pids):
        return []
    rows = db.execute(text("""
        WITH RECURSIVE direct AS (
            SELECT pm.portfolio_id FROM portfolio_members pm WHERE pm.version_id = ANY(:vids)
            UNION
            SELECT pm.portfolio_id FROM portfolio_members pm JOIN branches b ON b.id = pm.branch_id
             WHERE b.head_version_id = ANY(:vids) OR b.id = ANY(:bids)
            UNION
            SELECT unnest(CAST(:pids AS bigint[]))
        ), up AS (
            SELECT portfolio_id FROM direct
            UNION
            SELECT pm.portfolio_id FROM portfolio_members pm JOIN up ON pm.child_portfolio_id = up.portfolio_id
        )
        SELECT DISTINCT portfolio_id FROM up
    """), {"vids": vids, "bids": bids, "pids": pids}).scalars().all()
    return list(rows)


def mark_stale(db: Session, pids: list[int]) -> None:
    if pids:
        db.execute(text("UPDATE portfolio_results SET stale = true WHERE portfolio_id = ANY(:ids)"), {"ids": pids})


def refresh_portfolios(pids: list[int]) -> None:
    """Background task: recompute stale portfolios (skips ones locked by another worker)."""
    from portofion_server.db import session_factory

    for pid in pids:
        db = session_factory()()
        try:
            got = db.execute(text("SELECT pg_try_advisory_xact_lock(:ns, :id)"), {"ns": LOCK_NS, "id": pid}).scalar()
            if not got:
                db.rollback()
                continue
            pf = db.get(Portfolio, pid)
            if pf is None:
                db.rollback()
                continue
            ws = db.get(Workspace, pf.workspace_id)
            db.commit()  # release; compute_result takes its own lock
            with contextlib.suppress(ApiError):
                compute_result(db, ws, pf)
        except Exception:  # pragma: no cover - background safety
            logger.exception("portfolio refresh failed for {}", pid)
            db.rollback()
        finally:
            db.close()
