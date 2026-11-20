from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from portofion_server.auth.security import parse_api_key, secret_matches, sha256
from portofion_server.db import get_db
from portofion_server.db.models import ApiKey, Membership, Session, User, Workspace
from portofion_server.errors import ApiError, forbidden, not_found
from portofion_server.settings import get_settings

SESSION_COOKIE = "pfn_session"
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User | None = None
    key: ApiKey | None = None

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user else None

    @property
    def key_id(self) -> int | None:
        return self.key.id if self.key else None

    @property
    def label(self) -> str:
        if self.user:
            return f"user:{self.user.email}"
        if self.key:
            return f"key:{self.key.name}"
        return "anonymous"


@dataclass
class WsCtx:
    ws: Workspace
    role: str
    principal: Principal

    @property
    def ws_id(self) -> int:
        return self.ws.id

    def require(self, role: str) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[role]:
            raise forbidden(f"requires {role} role")

    def require_user(self, role: str = "member") -> User:
        if self.principal.user is None:
            raise forbidden("this action requires a signed-in user")
        self.require(role)
        return self.principal.user

    def require_scope(self, scope: str) -> None:
        k = self.principal.key
        if k is not None and scope not in (k.scopes or []):
            raise forbidden(f"API key lacks the '{scope}' scope")


def _check_origin(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if not origin:
        return
    cfg = get_settings()
    allowed = {request.headers.get("host", ""), urlparse(cfg.base_url).netloc}
    fwd = request.headers.get("x-forwarded-host")
    if fwd:
        allowed.add(fwd.split(",")[0].strip())
    allowed |= {urlparse(o.strip()).netloc for o in cfg.trusted_origins.split(",") if o.strip()}
    if urlparse(origin).netloc not in allowed:
        raise ApiError(403, "BAD_ORIGIN", "cross-origin request rejected",
                       hint="set PORTOFION_BASE_URL (or PORTOFION_TRUSTED_ORIGINS) to the URL users open")


def _key_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def get_principal(request: Request, db: DbSession = Depends(get_db)) -> Principal:
    now = datetime.now(UTC)
    raw_key = _key_from_request(request)
    if raw_key:
        parsed = parse_api_key(raw_key)
        if not parsed:
            raise ApiError(401, "UNAUTHORIZED", "malformed API key", hint="keys look like pfn_XXXXXXXX_...")
        prefix, secret = parsed
        key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
        if (
            key is None
            or not secret_matches(secret, key.secret_hash)
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at < now)
        ):
            raise ApiError(401, "UNAUTHORIZED", "invalid or revoked API key")
        if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
            key.last_used_at = now
            db.commit()
        return Principal(key=key)

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sess = db.get(Session, sha256(token))
        if sess is not None and sess.expires_at > now:
            user = db.get(User, sess.user_id)
            if user is not None and user.disabled_at is None:
                _check_origin(request)
                if now - sess.last_seen_at > timedelta(minutes=10):
                    sess.last_seen_at = now
                    sess.expires_at = now + timedelta(days=get_settings().session_days)
                    db.commit()
                return Principal(user=user)
    return Principal()


def require_user(principal: Principal = Depends(get_principal)) -> User:
    if principal.user is None:
        raise ApiError(401, "UNAUTHORIZED", "sign in required")
    return principal.user


def require_superuser(user: User = Depends(require_user)) -> User:
    if not user.is_superuser:
        raise forbidden("superuser only")
    return user


def resolve_ws(db: DbSession, principal: Principal, ws_slug: str) -> WsCtx:
    ws = db.scalar(select(Workspace).where(Workspace.slug == ws_slug))
    if ws is None:
        raise not_found("workspace")
    if principal.key is not None:
        if principal.key.workspace_id != ws.id:
            raise not_found("workspace")
        if "read" not in (principal.key.scopes or []) and "ingest" not in (principal.key.scopes or []):
            raise forbidden("API key has no usable scope")
        return WsCtx(ws, "viewer", principal)
    if principal.user is None:
        raise ApiError(401, "UNAUTHORIZED", "sign in required")
    if principal.user.is_superuser:
        return WsCtx(ws, "admin", principal)
    m = db.get(Membership, (ws.id, principal.user.id))
    if m is None:
        raise not_found("workspace")
    return WsCtx(ws, m.role, principal)


def ws_ctx(ws: str, principal: Principal = Depends(get_principal), db: DbSession = Depends(get_db)) -> WsCtx:
    return resolve_ws(db, principal, ws)


def key_ctx(principal: Principal = Depends(get_principal), db: DbSession = Depends(get_db)) -> WsCtx:
    """Workspace context for agent endpoints authenticated by API key."""
    if principal.key is None:
        raise ApiError(401, "UNAUTHORIZED", "API key required",
                       hint="send 'Authorization: Bearer pfn_...'")
    ws = db.get(Workspace, principal.key.workspace_id)
    return WsCtx(ws, "viewer", principal)
