"""Agent-facing endpoints (API key auth): ingest, weights, status, read-back."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portofion_server.auth.deps import WsCtx, key_ctx
from portofion_server.auth.security import sha256
from portofion_server.db import get_db
from portofion_server.db.models import Branch, Series, Strategy, Version
from portofion_server.domain.catalog import version_series
from portofion_server.domain.ingest import find_replay, ingest_item
from portofion_server.domain.portfolios import (
    dependents,
    mark_stale,
    refresh_portfolios,
    report_weights,
)
from portofion_server.domain.refs import resolve_version_ref
from portofion_server.errors import ApiError, not_found
from portofion_server.schemas import IngestBatchIn, IngestIn, PortfolioWeightsIn
from portofion_server.settings import public_base

router = APIRouter(tags=["agent"])


def _absolute(resp: dict, base: str) -> dict:
    url = resp.get("url")
    return {**resp, "url": base + url} if isinstance(url, str) and url.startswith("/") else resp


def run_ingest(db: Session, ctx: WsCtx, body: IngestIn, idem_key: str | None, bg: BackgroundTasks,
               base: str) -> dict:
    return _absolute(_run_ingest(db, ctx, body, idem_key, bg), base)


def _run_ingest(db: Session, ctx: WsCtx, body: IngestIn, idem_key: str | None, bg: BackgroundTasks) -> dict:
    ctx.require_scope("ingest")
    if ctx.principal.user is not None:
        ctx.require("member")
    sha = sha256(body.model_dump_json())
    if idem_key and not body.dry_run:
        replay = find_replay(db, ctx.ws_id, idem_key, sha)
        if replay is not None:
            return replay
    try:
        out = ingest_item(db, ctx, body, idempotency_key=None if body.dry_run else idem_key, request_sha=sha)
    except ApiError:
        db.rollback()
        raise
    except IntegrityError as e:
        db.rollback()
        if idem_key:
            replay = find_replay(db, ctx.ws_id, idem_key, sha)
            if replay is not None:
                return replay
        raise ApiError(409, "CONFLICT", "concurrent write conflict; retry the request") from e
    if body.dry_run:
        db.rollback()
        return out.response
    pids = dependents(db, version_ids=out.changed_versions, branch_ids=out.moved_branches)
    mark_stale(db, pids)
    db.commit()
    if pids:
        bg.add_task(refresh_portfolios, pids)
    return out.response


def run_batch(db: Session, ctx: WsCtx, body: IngestBatchIn, bg: BackgroundTasks, base: str) -> dict:
    ctx.require_scope("ingest")
    if ctx.principal.user is not None:
        ctx.require("member")
    results = []
    changed, moved = [], []
    n_ok = 0
    for i, item in enumerate(body.items):
        sp = db.begin_nested()
        try:
            out = ingest_item(db, ctx, item)
            if item.dry_run:
                sp.rollback()
            else:
                sp.commit()
                changed += out.changed_versions
                moved += out.moved_branches
            results.append({**_absolute(out.response, base), "index": i})
            n_ok += 1
        except ApiError as e:
            sp.rollback()
            results.append({**e.body(), "index": i})
    pids = dependents(db, version_ids=changed, branch_ids=moved)
    mark_stale(db, pids)
    db.commit()
    if pids:
        bg.add_task(refresh_portfolios, pids)
    return {"ok": n_ok == len(body.items), "n_ok": n_ok, "n_failed": len(body.items) - n_ok, "results": results}


@router.post("/ingest")
def ingest(body: IngestIn, bg: BackgroundTasks, request: Request, ctx: WsCtx = Depends(key_ctx),
           db: Session = Depends(get_db), idempotency_key: str | None = Header(default=None)):
    return run_ingest(db, ctx, body, idempotency_key, bg, public_base(request))


@router.post("/ingest/batch")
def ingest_batch(body: IngestBatchIn, bg: BackgroundTasks, request: Request, ctx: WsCtx = Depends(key_ctx),
                 db: Session = Depends(get_db)):
    return run_batch(db, ctx, body, bg, public_base(request))


@router.put("/ingest/portfolio-weights")
def ingest_weights(body: PortfolioWeightsIn, bg: BackgroundTasks, ctx: WsCtx = Depends(key_ctx),
                   db: Session = Depends(get_db)):
    ctx.require_scope("ingest")
    slug = body.portfolio[3:] if body.portfolio.startswith("pf:") else body.portfolio
    out = report_weights(db, ctx, slug, body.reports, auto_add=body.auto_add_members, replace=body.replace)
    bg.add_task(_refresh_slug, ctx.ws_id, slug)
    return out


def _refresh_slug(ws_id: int, slug: str) -> None:
    from portofion_server.db import session_factory
    from portofion_server.db.models import Portfolio

    db = session_factory()()
    try:
        pid = db.scalar(select(Portfolio.id).where(Portfolio.workspace_id == ws_id, Portfolio.slug == slug))
        ids = dependents(db, portfolio_ids=[pid]) if pid else []
    finally:
        db.close()
    if ids:
        refresh_portfolios(ids)


@router.get("/agent/whoami")
def whoami(ctx: WsCtx = Depends(key_ctx)):
    k = ctx.principal.key
    return {"workspace": ctx.ws.slug, "key": k.name, "prefix": f"pfn_{k.prefix}", "scopes": k.scopes}


@router.get("/agent/status")
def status(strategy: str | None = None, ctx: WsCtx = Depends(key_ctx), db: Session = Depends(get_db)):
    """Where each series stands, so an agent knows where to resume."""
    q = select(Strategy).where(Strategy.workspace_id == ctx.ws_id)
    if strategy:
        q = q.where(Strategy.slug == strategy)
    strategies = db.scalars(q.order_by(Strategy.slug)).all()
    if strategy and not strategies:
        raise not_found("strategy")
    sids = [s.id for s in strategies]
    branches = db.scalars(select(Branch).where(Branch.strategy_id.in_(sids))).all() if sids else []
    versions = db.scalars(select(Version).where(Version.strategy_id.in_(sids)).order_by(Version.seq)).all() if sids else []
    vids = [v.id for v in versions]
    series = db.scalars(select(Series).where(Series.version_id.in_(vids))).all() if vids else []
    ser_by_v: dict[int, dict] = {}
    for s in series:
        ser_by_v.setdefault(s.version_id, {})[s.name] = {
            "first_date": s.first_date.isoformat() if s.first_date else None,
            "last_date": s.last_date.isoformat() if s.last_date else None,
            "n_points": s.n_points, "data_rev": s.data_rev}
    v_by_b: dict[int, list] = {}
    for v in versions:
        v_by_b.setdefault(v.branch_id, []).append(
            {"label": v.label, "stage": v.stage, "params_fingerprint": v.fingerprint, "code_ref": v.code_ref,
             "series": ser_by_v.get(v.id, {})})
    b_by_s: dict[int, list] = {}
    heads = {v.id: v.label for v in versions}
    for b in branches:
        b_by_s.setdefault(b.strategy_id, []).append(
            {"name": b.name, "head": heads.get(b.head_version_id), "head_locked": b.head_locked,
             "versions": v_by_b.get(b.id, [])})
    return {"workspace": ctx.ws.slug,
            "strategies": [{"slug": s.slug, "name": s.name, "kind": s.kind, "branches": b_by_s.get(s.id, [])}
                           for s in strategies]}


@router.get("/agent/series")
def agent_series(ref: str, names: str | None = Query(default=None), ctx: WsCtx = Depends(key_ctx),
                 db: Session = Depends(get_db)):
    ctx.require_scope("read")
    v = resolve_version_ref(db, ctx.ws_id, ref)
    return version_series(db, ctx, v.id, names.split(",") if names else None)
