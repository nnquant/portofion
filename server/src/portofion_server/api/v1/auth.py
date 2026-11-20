from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from portofion_server.auth.deps import (
    SESSION_COOKIE,
    Principal,
    get_principal,
    require_superuser,
    require_user,
)
from portofion_server.auth.security import verify_password
from portofion_server.db import get_db
from portofion_server.db.models import User
from portofion_server.domain import accounts
from portofion_server.errors import ApiError
from portofion_server.schemas import InviteAcceptIn, LoginIn, SignupIn, UserCreateIn, WorkspaceIn
from portofion_server.settings import get_settings

router = APIRouter(tags=["auth"])

_FAILS: dict[str, deque] = defaultdict(deque)
WINDOW_S = 15 * 60
MAX_FAILS = 10


def _throttled(key: str) -> bool:
    q = _FAILS[key]
    now = time.monotonic()
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    return len(q) >= MAX_FAILS


def _set_cookie(response: Response, token: str) -> None:
    cfg = get_settings()
    response.set_cookie(SESSION_COOKIE, token, max_age=cfg.session_days * 86400, httponly=True,
                        secure=cfg.cookie_secure, samesite="lax", path="/")


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    key = f"{ip}|{body.email.strip().lower()}"
    if _throttled(key):
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "too many failed sign-ins; try again later")
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or user.disabled_at is not None or not verify_password(body.password, user.password_hash):
        _FAILS[key].append(time.monotonic())
        raise ApiError(401, "UNAUTHORIZED", "wrong email or password")
    _FAILS.pop(key, None)
    token = accounts.create_session(db, user, ip, request.headers.get("user-agent"))
    _set_cookie(response, token)
    return accounts.me(db, user)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    accounts.end_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return accounts.me(db, user)


@router.get("/auth/config")
def auth_config():
    return {"allow_signup": get_settings().allow_signup}


@router.post("/auth/signup")
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not get_settings().allow_signup:
        raise ApiError(403, "SIGNUP_DISABLED", "sign-up is disabled; ask an admin for an invite link")
    user = accounts.create_user(db, body.email, body.password, body.name)
    db.commit()
    token = accounts.create_session(db, user, request.client.host if request.client else None,
                                    request.headers.get("user-agent"))
    _set_cookie(response, token)
    return accounts.me(db, user)


@router.get("/invites/{token}")
def invite_info(token: str, db: Session = Depends(get_db)):
    return accounts.invite_info(db, token)


@router.post("/invites/{token}/accept")
def invite_accept(token: str, body: InviteAcceptIn, request: Request, response: Response,
                  principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    user, ws, new_session = accounts.accept_invite(db, token, principal.user, body.email, body.password, body.name)
    if new_session:
        tok = accounts.create_session(db, user, request.client.host if request.client else None,
                                      request.headers.get("user-agent"))
        _set_cookie(response, tok)
    return {"workspace": ws.slug, "me": accounts.me(db, user)}


@router.post("/workspaces")
def create_workspace(body: WorkspaceIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    ws = accounts.create_workspace(db, user, body.slug, body.name)
    return {"slug": ws.slug, "name": ws.name, "kind": ws.kind}


@router.get("/admin/users")
def users(_: User = Depends(require_superuser), db: Session = Depends(get_db)):
    return accounts.list_users(db)


@router.post("/admin/users")
def user_create(body: UserCreateIn, _: User = Depends(require_superuser), db: Session = Depends(get_db)):
    u = accounts.create_user(db, body.email, body.password, body.name, is_superuser=body.is_superuser)
    db.commit()
    return {"id": u.id, "email": u.email}
