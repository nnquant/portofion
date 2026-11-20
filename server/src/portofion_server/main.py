from __future__ import annotations

import gzip
import threading
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from loguru import logger
from sqlalchemy import select

from portofion_engine import ENGINE_VERSION
from portofion_server import skillpack
from portofion_server.api.v1 import agent, auth, workspace
from portofion_server.errors import ApiError, install_handlers
from portofion_server.settings import get_settings, public_base

PKG_DIR = Path(str(files("portofion_server")))
REPO_ROOT = PKG_DIR.parents[2]


def _first_existing(*paths: Path | None) -> Path | None:
    for p in paths:
        if p is not None and p.exists():
            return p
    return None


def static_dir() -> Path | None:
    cfg = get_settings()
    return _first_existing(Path(cfg.static_dir) if cfg.static_dir else None, PKG_DIR / "static",
                           REPO_ROOT / "web" / "dist")


def sdk_dir() -> Path | None:
    cfg = get_settings()
    return _first_existing(Path(cfg.sdk_dir) if cfg.sdk_dir else None, PKG_DIR / "sdk", REPO_ROOT / "dist")


class RequestBodyMiddleware:
    """Rejects oversized bodies and transparently gunzips ``Content-Encoding: gzip`` requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await self.app(scope, receive, send)
        cfg = get_settings()
        limit = cfg.max_body_mb * 1024 * 1024
        headers = dict(scope["headers"])
        enc = headers.get(b"content-encoding", b"").decode().lower()
        clen = headers.get(b"content-length")
        if clen and int(clen) > limit:
            return await _reject(send, 413, "BATCH_TOO_LARGE", f"request body larger than {cfg.max_body_mb} MB")
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
            if len(body) > limit:
                return await _reject(send, 413, "BATCH_TOO_LARGE", f"request body larger than {cfg.max_body_mb} MB")
        if enc == "gzip":
            try:
                body = gzip.decompress(body)
            except OSError:
                return await _reject(send, 400, "BAD_REQUEST", "invalid gzip body")
            if len(body) > 4 * limit:
                return await _reject(send, 413, "BATCH_TOO_LARGE", "decompressed body too large")
            new_headers = [(k, v) for k, v in scope["headers"] if k not in (b"content-encoding", b"content-length")]
            new_headers.append((b"content-length", str(len(body)).encode()))
            scope = {**scope, "headers": new_headers}
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


async def _reject(send, status: int, code: str, message: str):
    import json

    payload = json.dumps(ApiError(status, code, message).body()).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


def _bootstrap() -> None:
    from portofion_server.db import session_factory
    from portofion_server.db.models import User
    from portofion_server.domain.accounts import create_user

    cfg = get_settings()
    if not (cfg.bootstrap_admin_email and cfg.bootstrap_admin_password):
        return
    db = session_factory()()
    try:
        if db.scalar(select(User.id).limit(1)) is None:
            create_user(db, cfg.bootstrap_admin_email, cfg.bootstrap_admin_password, "admin", is_superuser=True)
            db.commit()
            logger.info("bootstrapped superuser {}", cfg.bootstrap_admin_email)
    finally:
        db.close()


def _recompute_stale() -> None:
    from portofion_server.db import session_factory
    from portofion_server.db.models import Workspace
    from portofion_server.domain.metrics_cache import recompute_workspace

    db = session_factory()()
    try:
        for ws in db.scalars(select(Workspace)).all():
            n = recompute_workspace(db, ws)
            db.commit()
            if n:
                logger.info("recomputed {} versions in {}", n, ws.slug)
    except Exception:  # pragma: no cover
        logger.exception("startup recompute failed")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.startup_tasks:
        try:
            _bootstrap()
        except Exception:  # pragma: no cover
            logger.exception("bootstrap failed")
        threading.Thread(target=_recompute_stale, daemon=True).start()
    yield


def create_app(*, startup_tasks: bool = True) -> FastAPI:
    app = FastAPI(title="portofion", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs", openapi_url="/openapi.json", redoc_url=None)
    app.state.startup_tasks = startup_tasks
    install_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestBodyMiddleware)
    for r in (agent.router, auth.router, workspace.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "engine": ENGINE_VERSION}

    def wheel_name() -> str | None:
        d = sdk_dir()
        wheels = sorted(d.glob("portofion_client-*.whl")) if d else []
        return wheels[-1].name if wheels else None

    @app.get("/api/v1/meta", include_in_schema=False)
    def meta():
        w = wheel_name()
        return {"version": app.version, "engine": ENGINE_VERSION, "sdk_wheel": f"/sdk/{w}" if w else None}

    def wheel_url(base: str) -> str:
        wheel = wheel_name()
        return f"{base}/sdk/{wheel}" if wheel else f"{base}/sdk/ (not built)"

    @app.get("/llms.txt", include_in_schema=False)
    def llms(request: Request):
        base = public_base(request)
        return PlainTextResponse(skillpack.llms_text(base, wheel_url(base)), media_type="text/markdown; charset=utf-8")

    @app.get("/skill/portofion.zip", include_in_schema=False)
    def skill_zip(request: Request):
        base = public_base(request)
        return Response(skillpack.bundle("zip", base, wheel_url(base)), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="portofion-skill.zip"'})

    @app.get("/skill/portofion.tar.gz", include_in_schema=False)
    def skill_tgz(request: Request):
        base = public_base(request)
        return Response(skillpack.bundle("tgz", base, wheel_url(base)), media_type="application/gzip",
                        headers={"Content-Disposition": 'attachment; filename="portofion-skill.tar.gz"'})

    @app.get("/skill/SKILL.md", include_in_schema=False)
    def skill_md(request: Request):
        base = public_base(request)
        return PlainTextResponse(skillpack.skill_files(base, wheel_url(base))["SKILL.md"],
                                 media_type="text/markdown; charset=utf-8")

    @app.get("/skill/install.sh", include_in_schema=False)
    def skill_install_sh(request: Request):
        return PlainTextResponse(skillpack.installer("sh", public_base(request)), media_type="text/x-shellscript")

    @app.get("/skill/install.ps1", include_in_schema=False)
    def skill_install_ps1(request: Request):
        return PlainTextResponse(skillpack.installer("ps1", public_base(request)), media_type="text/plain")

    @app.get("/sdk/{name}", include_in_schema=False)
    def sdk(name: str):
        d = sdk_dir()
        if d is None or "/" in name or "\\" in name or not name.endswith((".whl", ".tar.gz")):
            return JSONResponse(ApiError(404, "NOT_FOUND", "not found").body(), status_code=404)
        p = d / name
        if not p.exists():
            return JSONResponse(ApiError(404, "NOT_FOUND", "not found").body(), status_code=404)
        return FileResponse(p, media_type="application/octet-stream", filename=name)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "sdk/", "skill/")):
            return JSONResponse(ApiError(404, "NOT_FOUND", "not found").body(), status_code=404)
        d = static_dir()
        if d is None:
            return PlainTextResponse("portofion API is running; web UI not built (cd web && npm run build)")
        f = (d / path).resolve()
        if path and f.is_file() and d.resolve() in f.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
            return FileResponse(f, headers=headers)
        return FileResponse(d / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
