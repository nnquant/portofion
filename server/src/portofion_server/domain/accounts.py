"""Users, workspaces, memberships, invites, API keys, settings, audit log."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from portofion_server.auth.deps import Principal, WsCtx
from portofion_server.auth.security import hash_password, new_api_key, new_token, sha256
from portofion_server.db.models import (
    ApiKey,
    AuditLog,
    Invite,
    Membership,
    User,
    Workspace,
)
from portofion_server.db.models import Session as SessionRow
from portofion_server.domain.common import audit, check_slug, ws_settings
from portofion_server.errors import ApiError, forbidden, not_found
from portofion_server.settings import get_settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _norm_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e):
        raise ApiError(422, "VALIDATION_ERROR", "invalid email")
    return e


def _personal_slug(db: Session, email: str) -> str:
    base = re.sub(r"[^a-z0-9._-]+", "-", email.split("@")[0].lower()).strip("-._") or "me"
    base = base[:48]
    slug = base
    i = 2
    while db.scalar(select(Workspace.id).where(Workspace.slug == slug)):
        slug = f"{base}-{i}"
        i += 1
    return slug


def create_user(db: Session, email: str, password: str | None, name: str = "", *,
                is_superuser: bool = False) -> User:
    email = _norm_email(email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise ApiError(409, "CONFLICT", "a user with this email already exists")
    user = User(email=email, name=name or email.split("@")[0],
                password_hash=hash_password(password) if password else None, is_superuser=is_superuser)
    db.add(user)
    db.flush()
    ws = Workspace(slug=_personal_slug(db, email), name=user.name, kind="personal", settings={})
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=user.id, role="admin"))
    audit(db, ws.id, None, "user.create", "user", user.id, after={"email": email})
    db.flush()
    return user


def create_session(db: Session, user: User, ip: str | None, ua: str | None) -> str:
    token = new_token()
    now = datetime.now(UTC)
    db.add(SessionRow(token_hash=sha256(token), user_id=user.id, expires_at=now + timedelta(days=get_settings().session_days),
                      ip=ip, user_agent=(ua or "")[:300]))
    db.commit()
    return token


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(SessionRow).where(SessionRow.token_hash == sha256(token)))
        db.commit()


def me(db: Session, user: User) -> dict:
    rows = db.execute(
        select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id).order_by(Workspace.kind.desc(), Workspace.slug)
    ).all()
    wss = [{"slug": w.slug, "name": w.name, "kind": w.kind, "role": r} for w, r in rows]
    if user.is_superuser:
        have = {w["slug"] for w in wss}
        for w in db.scalars(select(Workspace).order_by(Workspace.slug)):
            if w.slug not in have:
                wss.append({"slug": w.slug, "name": w.name, "kind": w.kind, "role": "admin", "implicit": True})
    return {"id": user.id, "email": user.email, "name": user.name, "is_superuser": user.is_superuser,
            "workspaces": wss}


def create_workspace(db: Session, user: User, slug: str, name: str) -> Workspace:
    check_slug(slug, "workspace slug")
    if db.scalar(select(Workspace.id).where(Workspace.slug == slug)):
        raise ApiError(409, "CONFLICT", f"workspace {slug!r} already exists")
    ws = Workspace(slug=slug, name=name or slug, kind="team", settings={})
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=user.id, role="admin"))
    audit(db, ws.id, Principal(user=user), "workspace.create", "workspace", ws.id, after={"slug": slug})
    db.commit()
    return ws


def workspace_info(ctx: WsCtx) -> dict:
    return {"slug": ctx.ws.slug, "name": ctx.ws.name, "kind": ctx.ws.kind, "role": ctx.role,
            "settings": ws_settings(ctx.ws), "settings_rev": ctx.ws.settings_rev}


def patch_settings(db: Session, ctx: WsCtx, patch: dict) -> bool:
    """Returns True when metric-relevant settings changed (caller recomputes)."""
    ctx.require_user("admin")
    before = ws_settings(ctx.ws)
    name = patch.pop("name", None)
    if name:
        ctx.ws.name = name
    new = {**(ctx.ws.settings or {}), **{k: v for k, v in patch.items() if v is not None}}
    metric_keys = ("periods_per_year", "rf", "var_level", "stale_days")
    changed = any(new.get(k, before[k]) != before[k] for k in metric_keys)
    ctx.ws.settings = new
    ctx.ws.settings_rev += 1
    audit(db, ctx.ws_id, ctx.principal, "workspace.settings", "workspace", ctx.ws_id, before, ws_settings(ctx.ws))
    db.commit()
    return changed


# --- members & invites ---------------------------------------------------------------------


def list_members(db: Session, ctx: WsCtx) -> list[dict]:
    rows = db.execute(
        select(User, Membership.role, Membership.created_at).join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == ctx.ws_id).order_by(User.email)
    ).all()
    return [{"user_id": u.id, "email": u.email, "name": u.name, "role": r, "since": t.isoformat()}
            for u, r, t in rows]


def _admin_count(db: Session, ws_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Membership)
                     .where(Membership.workspace_id == ws_id, Membership.role == "admin")) or 0


def set_role(db: Session, ctx: WsCtx, user_id: int, role: str) -> None:
    ctx.require_user("admin")
    m = db.get(Membership, (ctx.ws_id, user_id))
    if m is None:
        raise not_found("member")
    if m.role == "admin" and role != "admin" and _admin_count(db, ctx.ws_id) <= 1:
        raise ApiError(409, "LAST_ADMIN", "a workspace needs at least one admin")
    audit(db, ctx.ws_id, ctx.principal, "member.role", "user", user_id, {"role": m.role}, {"role": role})
    m.role = role
    db.commit()


def remove_member(db: Session, ctx: WsCtx, user_id: int) -> None:
    if ctx.principal.user_id != user_id:
        ctx.require_user("admin")
    m = db.get(Membership, (ctx.ws_id, user_id))
    if m is None:
        raise not_found("member")
    if ctx.ws.kind == "personal":
        raise forbidden("cannot leave a personal workspace")
    if m.role == "admin" and _admin_count(db, ctx.ws_id) <= 1:
        raise ApiError(409, "LAST_ADMIN", "a workspace needs at least one admin")
    audit(db, ctx.ws_id, ctx.principal, "member.remove", "user", user_id, {"role": m.role})
    db.delete(m)
    db.commit()


def create_invite(db: Session, ctx: WsCtx, email: str | None, role: str, days: int) -> dict:
    ctx.require_user("admin")
    token = new_token()
    inv = Invite(workspace_id=ctx.ws_id, email=_norm_email(email) if email else None, role=role,
                 token_hash=sha256(token), created_by=ctx.principal.user_id,
                 expires_at=datetime.now(UTC) + timedelta(days=days))
    db.add(inv)
    db.flush()
    audit(db, ctx.ws_id, ctx.principal, "invite.create", "invite", inv.id, after={"email": inv.email, "role": role})
    db.commit()
    url = f"{get_settings().base_url.rstrip('/')}/invite/{token}"
    return {"id": inv.id, "url": url, "token": token, "role": role, "email": inv.email,
            "expires_at": inv.expires_at.isoformat()}


def list_invites(db: Session, ctx: WsCtx) -> list[dict]:
    ctx.require_user("admin")
    rows = db.scalars(select(Invite).where(Invite.workspace_id == ctx.ws_id, Invite.accepted_at.is_(None))
                      .order_by(Invite.id.desc())).all()
    now = datetime.now(UTC)
    return [{"id": i.id, "email": i.email, "role": i.role, "expires_at": i.expires_at.isoformat(),
             "expired": i.expires_at < now} for i in rows]


def revoke_invite(db: Session, ctx: WsCtx, invite_id: int) -> None:
    ctx.require_user("admin")
    inv = db.get(Invite, invite_id)
    if inv is None or inv.workspace_id != ctx.ws_id:
        raise not_found("invite")
    db.delete(inv)
    db.commit()


def invite_info(db: Session, token: str) -> dict:
    inv = db.scalar(select(Invite).where(Invite.token_hash == sha256(token)))
    if inv is None or inv.accepted_at is not None or inv.expires_at < datetime.now(UTC):
        raise not_found("invite")
    ws = db.get(Workspace, inv.workspace_id)
    return {"workspace": ws.name, "workspace_slug": ws.slug, "role": inv.role, "email": inv.email}


def accept_invite(db: Session, token: str, current: User | None, email: str | None, password: str | None,
                  name: str) -> tuple[User, Workspace, bool]:
    """Returns (user, workspace, new_session_needed)."""
    inv = db.scalar(select(Invite).where(Invite.token_hash == sha256(token)).with_for_update())
    if inv is None or inv.accepted_at is not None or inv.expires_at < datetime.now(UTC):
        raise not_found("invite")
    new_session = False
    if current is None:
        if not email or not password:
            raise ApiError(422, "VALIDATION_ERROR", "email and password required")
        email = _norm_email(email)
        if inv.email and inv.email != email:
            raise forbidden("invite is for a different email")
        if db.scalar(select(User.id).where(User.email == email)):
            raise ApiError(409, "CONFLICT", "account exists; sign in first, then open the invite link")
        user = create_user(db, email, password, name)
        new_session = True
    else:
        user = current
        if inv.email and inv.email != user.email:
            raise forbidden("invite is for a different email")
    m = db.get(Membership, (inv.workspace_id, user.id))
    if m is None:
        db.add(Membership(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role))
    inv.accepted_at = datetime.now(UTC)
    inv.accepted_by = user.id
    audit(db, inv.workspace_id, Principal(user=user), "invite.accept", "invite", inv.id)
    db.commit()
    return user, db.get(Workspace, inv.workspace_id), new_session


# --- API keys --------------------------------------------------------------------------------


def list_keys(db: Session, ctx: WsCtx) -> list[dict]:
    user = ctx.require_user("viewer")
    q = select(ApiKey, User.email).outerjoin(User, User.id == ApiKey.created_by).where(ApiKey.workspace_id == ctx.ws_id)
    if ctx.role != "admin":
        q = q.where(ApiKey.created_by == user.id)
    rows = db.execute(q.order_by(ApiKey.id.desc())).all()
    return [{"id": k.id, "name": k.name, "prefix": f"pfn_{k.prefix}", "scopes": k.scopes, "created_by": email,
             "created_at": k.created_at.isoformat(),
             "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
             "expires_at": k.expires_at.isoformat() if k.expires_at else None,
             "revoked": k.revoked_at is not None} for k, email in rows]


def create_key(db: Session, ctx: WsCtx, name: str, scopes: list[str], expires_days: int | None) -> dict:
    user = ctx.require_user("member")
    full, prefix, secret_hash = new_api_key()
    k = ApiKey(workspace_id=ctx.ws_id, name=name, prefix=prefix, secret_hash=secret_hash,
               scopes=sorted(set(scopes)) or ["ingest", "read"], created_by=user.id,
               expires_at=datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None)
    db.add(k)
    db.flush()
    audit(db, ctx.ws_id, ctx.principal, "key.create", "api_key", k.id, after={"name": name, "scopes": k.scopes})
    db.commit()
    return {"id": k.id, "name": name, "key": full, "prefix": f"pfn_{prefix}", "scopes": k.scopes}


def revoke_key(db: Session, ctx: WsCtx, key_id: int) -> None:
    user = ctx.require_user("member")
    k = db.get(ApiKey, key_id)
    if k is None or k.workspace_id != ctx.ws_id:
        raise not_found("api key")
    if ctx.role != "admin" and k.created_by != user.id:
        raise forbidden("only admins can revoke other members' keys")
    k.revoked_at = datetime.now(UTC)
    audit(db, ctx.ws_id, ctx.principal, "key.revoke", "api_key", k.id, {"name": k.name})
    db.commit()


# --- audit ---------------------------------------------------------------------------------


def audit_log(db: Session, ctx: WsCtx, limit: int = 300, before_id: int | None = None) -> list[dict]:
    ctx.require_user("admin")
    q = (select(AuditLog, User.email, ApiKey.name)
         .outerjoin(User, User.id == AuditLog.actor_user_id)
         .outerjoin(ApiKey, ApiKey.id == AuditLog.actor_key_id)
         .where(AuditLog.workspace_id == ctx.ws_id))
    if before_id:
        q = q.where(AuditLog.id < before_id)
    rows = db.execute(q.order_by(AuditLog.id.desc()).limit(limit)).all()
    return [{"id": a.id, "at": a.at.isoformat(), "actor": email or (f"key:{kname}" if kname else None),
             "action": a.action, "target_kind": a.target_kind, "target_id": a.target_id,
             "before": a.before, "after": a.after} for a, email, kname in rows]


def list_users(db: Session) -> list[dict]:
    return [{"id": u.id, "email": u.email, "name": u.name, "is_superuser": u.is_superuser,
             "disabled": u.disabled_at is not None, "created_at": u.created_at.isoformat()}
            for u in db.scalars(select(User).order_by(User.email))]
