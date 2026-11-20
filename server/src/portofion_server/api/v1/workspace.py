"""Workspace-scoped endpoints for the web UI (session cookie or read-scoped key)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from portofion_server.api.v1.agent import _refresh_slug, run_batch, run_ingest
from portofion_server.auth.deps import WsCtx, ws_ctx
from portofion_server.db import get_db
from portofion_server.domain import accounts, catalog, compare, portfolios
from portofion_server.domain.portfolios import dependents, mark_stale, refresh_portfolios
from portofion_server.errors import ApiError
from portofion_server.schemas import (
    ApiKeyIn,
    BranchPatch,
    FlagAckIn,
    IngestBatchIn,
    IngestIn,
    InviteIn,
    MemberRolePatch,
    PortfolioIn,
    PortfolioPreviewIn,
    StrategyPatch,
    VersionPatch,
    WeightReportIn,
    WorkspaceSettingsPatch,
)
from portofion_server.settings import public_base

router = APIRouter(prefix="/w/{ws}", tags=["workspace"])


def _etag(request: Request, response: Response, tag: str) -> bool:
    etag = f'W/"{tag}"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return request.headers.get("if-none-match") == etag


def _after_change(db: Session, bg: BackgroundTasks, *, versions=(), branches=(), pids=()) -> None:
    ids = dependents(db, version_ids=versions, branch_ids=branches, portfolio_ids=pids)
    mark_stale(db, ids)
    db.commit()
    if ids:
        bg.add_task(refresh_portfolios, ids)


# --- workspace -------------------------------------------------------------------------------


@router.get("")
def get_ws(ctx: WsCtx = Depends(ws_ctx)):
    return accounts.workspace_info(ctx)


@router.patch("/settings")
def patch_settings(body: WorkspaceSettingsPatch, bg: BackgroundTasks, ctx: WsCtx = Depends(ws_ctx),
                   db: Session = Depends(get_db)):
    changed = accounts.patch_settings(db, ctx, body.model_dump(exclude_unset=True))
    if changed:
        bg.add_task(recompute_ws_bg, ctx.ws_id)
    return accounts.workspace_info(ctx)


def recompute_ws_bg(ws_id: int) -> None:
    from sqlalchemy import select

    from portofion_server.db import session_factory
    from portofion_server.db.models import Portfolio, Workspace
    from portofion_server.domain.metrics_cache import recompute_workspace

    db = session_factory()()
    pids: list[int] = []
    try:
        ws = db.get(Workspace, ws_id)
        recompute_workspace(db, ws)
        db.commit()
        pids = list(db.scalars(select(Portfolio.id).where(Portfolio.workspace_id == ws_id)))
        mark_stale(db, pids)
        db.commit()
    finally:
        db.close()
    if pids:
        refresh_portfolios(pids)


@router.get("/members")
def members(ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return accounts.list_members(db, ctx)


@router.patch("/members/{user_id}")
def member_role(user_id: int, body: MemberRolePatch, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    accounts.set_role(db, ctx, user_id, body.role)
    return {"ok": True}


@router.delete("/members/{user_id}")
def member_remove(user_id: int, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    accounts.remove_member(db, ctx, user_id)
    return {"ok": True}


@router.get("/invites")
def invites(ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return accounts.list_invites(db, ctx)


@router.post("/invites")
def invite(body: InviteIn, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return accounts.create_invite(db, ctx, body.email, body.role, body.days)


@router.delete("/invites/{invite_id}")
def invite_revoke(invite_id: int, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    accounts.revoke_invite(db, ctx, invite_id)
    return {"ok": True}


@router.get("/keys")
def keys(ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return accounts.list_keys(db, ctx)


@router.post("/keys")
def key_create(body: ApiKeyIn, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return accounts.create_key(db, ctx, body.name, list(body.scopes), body.expires_days)


@router.delete("/keys/{key_id}")
def key_revoke(key_id: int, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    accounts.revoke_key(db, ctx, key_id)
    return {"ok": True}


@router.get("/audit")
def audit(before: int | None = None, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return accounts.audit_log(db, ctx, before_id=before)


# --- ingest from the UI (session) -------------------------------------------------------------


@router.post("/ingest")
def ingest(body: IngestIn, bg: BackgroundTasks, request: Request, ctx: WsCtx = Depends(ws_ctx),
           db: Session = Depends(get_db)):
    if ctx.principal.key is not None:
        raise ApiError(400, "BAD_REQUEST", "agents should POST /api/v1/ingest")
    return run_ingest(db, ctx, body, None, bg, public_base(request))


@router.post("/ingest/batch")
def ingest_batch(body: IngestBatchIn, bg: BackgroundTasks, request: Request, ctx: WsCtx = Depends(ws_ctx),
                 db: Session = Depends(get_db)):
    if ctx.principal.key is not None:
        raise ApiError(400, "BAD_REQUEST", "agents should POST /api/v1/ingest/batch")
    return run_batch(db, ctx, body, bg, public_base(request))


# --- catalog ---------------------------------------------------------------------------------


@router.get("/library")
def library(level: str = Query("branch", pattern="^(branch|version)$"),
            window: str = Query("all", pattern="^(all|live|ytd|1y|3y)$"),
            archived: bool = False, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.library(db, ctx, level=level, window=window, archived=archived)


@router.get("/search")
def search(q: str = "", ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.search(db, ctx, q) if q.strip() else []


@router.get("/resolve")
def resolve_ref(ref: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.resolve_for_ui(db, ctx, ref)


@router.get("/strategies/{slug}")
def strategy(slug: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.strategy_detail(db, ctx, slug)


@router.patch("/strategies/{slug}")
def strategy_patch(slug: str, body: StrategyPatch, bg: BackgroundTasks, ctx: WsCtx = Depends(ws_ctx),
                   db: Session = Depends(get_db)):
    changed, moved = catalog.patch_strategy(db, ctx, slug, body)
    _after_change(db, bg, versions=changed, branches=moved)
    return catalog.strategy_detail(db, ctx, slug)


@router.delete("/strategies/{slug}")
def strategy_delete(slug: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    catalog.delete_strategy(db, ctx, slug)
    return {"ok": True}


@router.patch("/strategies/{slug}/branches/{name}")
def branch_patch(slug: str, name: str, body: BranchPatch, bg: BackgroundTasks, ctx: WsCtx = Depends(ws_ctx),
                 db: Session = Depends(get_db)):
    moved = catalog.patch_branch(db, ctx, slug, name, body)
    _after_change(db, bg, branches=moved)
    return catalog.strategy_detail(db, ctx, slug)


@router.patch("/versions/{version_id}")
def version_patch(version_id: int, body: VersionPatch, bg: BackgroundTasks, ctx: WsCtx = Depends(ws_ctx),
                  db: Session = Depends(get_db)):
    if catalog.patch_version(db, ctx, version_id, body):
        _after_change(db, bg, versions=[version_id])
    return {"ok": True}


@router.delete("/versions/{version_id}")
def version_delete(version_id: int, bg: BackgroundTasks, ctx: WsCtx = Depends(ws_ctx),
                   db: Session = Depends(get_db)):
    v, _ = catalog.get_version(db, ctx, version_id)
    branch_id = v.branch_id
    catalog.delete_version(db, ctx, version_id)
    _after_change(db, bg, branches=[branch_id])
    return {"ok": True}


@router.get("/versions/{version_id}/analytics")
def analytics(version_id: int, request: Request, response: Response,
              window: str = Query("all", pattern="^(all|live|ytd|mtd|1y|3y|5y)$"),
              rolling: int = Query(126, ge=5, le=1260), benchmark: str | None = None,
              ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    out, tag = catalog.version_analytics(db, ctx, version_id, window=window, rolling=rolling, benchmark=benchmark)
    if _etag(request, response, tag):
        return Response(status_code=304, headers=dict(response.headers))
    return out


@router.get("/versions/{version_id}/series")
def series(version_id: int, names: str | None = None, format: str = Query("json", pattern="^(json|csv)$"),
           ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    out = catalog.version_series(db, ctx, version_id, names.split(",") if names else None, format)
    if format == "csv":
        return PlainTextResponse(out, media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="version-{version_id}.csv"'})
    return out


@router.get("/versions/{version_id}/revisions")
def revisions(version_id: int, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.version_revisions(db, ctx, version_id)


@router.get("/versions/{version_id}/flags")
def flags(version_id: int, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.version_flags(db, ctx, version_id)


@router.get("/versions/{version_id}/batches")
def batches(version_id: int, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.version_batches(db, ctx, version_id)


@router.get("/flags")
def ws_flags(open: bool = True, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return catalog.workspace_flags(db, ctx, open_only=open)


@router.post("/flags/ack")
def ack(body: FlagAckIn, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return {"acked": catalog.ack_flags(db, ctx, body.version_id, body.dates, body.codes)}


@router.get("/compare")
def compare_ep(request: Request, response: Response, refs: str,
               start: date | None = None, end: date | None = None,
               corr_freq: str = Query("D", pattern="^(D|W|M)$"), rolling: int = Query(63, ge=5, le=1260),
               ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    out, tag = compare.compare(db, ctx, refs.split(","), start=start, end=end, corr_freq=corr_freq, rolling=rolling)
    if _etag(request, response, tag):
        return Response(status_code=304, headers=dict(response.headers))
    return out


# --- portfolios ------------------------------------------------------------------------------


@router.get("/portfolios")
def pf_list(ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return portfolios.list_portfolios(db, ctx)


@router.post("/portfolios")
def pf_create(body: PortfolioIn, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    pf = portfolios.save_portfolio(db, ctx, body)
    return portfolios.portfolio_detail(db, ctx, pf.slug)


@router.post("/portfolios/preview")
def pf_preview(body: PortfolioPreviewIn, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return portfolios.preview(db, ctx, body.spec, body.members, body.slug)


@router.get("/portfolios/{slug}")
def pf_get(slug: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    return portfolios.portfolio_detail(db, ctx, slug)


@router.put("/portfolios/{slug}")
def pf_update(slug: str, body: PortfolioIn, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    pf = portfolios.save_portfolio(db, ctx, body, existing_slug=slug)
    return portfolios.portfolio_detail(db, ctx, pf.slug)


@router.delete("/portfolios/{slug}")
def pf_delete(slug: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    portfolios.delete_portfolio(db, ctx, slug)
    return {"ok": True}


@router.get("/portfolios/{slug}/result")
def pf_result(slug: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    pf = portfolios.get_portfolio(db, ctx, slug)
    return portfolios.compute_result(db, ctx.ws, pf)


@router.post("/portfolios/{slug}/compute")
def pf_compute(slug: str, ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    ctx.require_user("member")
    pf = portfolios.get_portfolio(db, ctx, slug)
    return portfolios.compute_result(db, ctx.ws, pf, force=True)


@router.put("/portfolios/{slug}/weights")
def pf_weights(slug: str, reports: list[WeightReportIn], bg: BackgroundTasks, replace: bool = False,
               ctx: WsCtx = Depends(ws_ctx), db: Session = Depends(get_db)):
    ctx.require_user("member")
    out = portfolios.report_weights(db, ctx, slug, reports, auto_add=False, replace=replace)
    bg.add_task(_refresh_slug, ctx.ws_id, slug)
    return out
