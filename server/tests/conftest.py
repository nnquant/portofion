"""Server tests run against a real Postgres.

Order of preference:
  1. PORTOFION_TEST_DATABASE_URL
  2. the dev compose database (127.0.0.1:55432), using a separate `portofion_test` database
  3. a throwaway testcontainers postgres:16-alpine
Tables are truncated between tests so commits and background tasks behave like production.
"""

from __future__ import annotations

import os

import psycopg
import pytest

DEV_ADMIN = "host=127.0.0.1 port=55432 user=portofion password=portofion dbname=portofion connect_timeout=3"


def _resolve_test_url() -> tuple[str, object | None]:
    url = os.environ.get("PORTOFION_TEST_DATABASE_URL")
    if url:
        return url, None
    try:
        with psycopg.connect(DEV_ADMIN, autocommit=True) as c:
            exists = c.execute("SELECT 1 FROM pg_database WHERE datname = 'portofion_test'").fetchone()
            if not exists:
                c.execute("CREATE DATABASE portofion_test")
        return "postgresql+psycopg://portofion:portofion@127.0.0.1:55432/portofion_test", None
    except psycopg.OperationalError:
        from testcontainers.postgres import PostgresContainer

        pg = PostgresContainer("postgres:16-alpine", driver="psycopg")
        pg.start()
        return pg.get_connection_url(), pg


_URL, _CONTAINER = _resolve_test_url()
os.environ["PORTOFION_DATABASE_URL"] = _URL
os.environ["PORTOFION_BASE_URL"] = "http://testserver"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from portofion_server.auth.deps import Principal, WsCtx  # noqa: E402
from portofion_server.db import get_engine, session_factory  # noqa: E402
from portofion_server.db.migrate import upgrade  # noqa: E402
from portofion_server.db.models import Workspace  # noqa: E402
from portofion_server.domain import accounts  # noqa: E402
from portofion_server.domain.series_store import clear_cache  # noqa: E402
from portofion_server.main import create_app  # noqa: E402

TABLES: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def _schema():
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    upgrade(url=_URL)
    with eng.connect() as c:
        TABLES.extend(
            r[0] for r in c.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename <> 'alembic_version' AND tablename NOT LIKE 'series_points_p%'"
            ))
        )
    yield
    eng.dispose()
    if _CONTAINER is not None:
        _CONTAINER.stop()


@pytest.fixture(autouse=True)
def _clean():
    with get_engine().begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    clear_cache()
    from portofion_server.api.v1 import auth as auth_mod

    auth_mod._FAILS.clear()
    yield


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


@pytest.fixture
def app():
    return create_app(startup_tasks=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class World:
    """Helpers to create users/keys and authenticated clients."""

    def __init__(self, app, db):
        self.app = app
        self.db = db

    def user(self, email="alice@example.com", password="password123", superuser=False):
        u = accounts.create_user(self.db, email, password, is_superuser=superuser)
        self.db.commit()
        return u

    def personal_ws(self, user) -> Workspace:
        return self.db.query(Workspace).filter(Workspace.name == user.name, Workspace.kind == "personal").one()

    def login(self, email="alice@example.com", password="password123") -> TestClient:
        c = TestClient(self.app)
        r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return c

    def key(self, ws: Workspace, user, scopes=("ingest", "read")) -> str:
        ctx = WsCtx(ws, "admin", Principal(user=user))
        return accounts.create_key(self.db, ctx, "test-key", list(scopes), None)["key"]

    def agent(self, key: str) -> TestClient:
        c = TestClient(self.app)
        c.headers["Authorization"] = f"Bearer {key}"
        return c


@pytest.fixture
def world(app, db):
    return World(app, db)


@pytest.fixture
def setup(world):
    """alice (admin of her personal workspace) + an agent client with an ingest/read key."""
    u = world.user()
    ws = world.personal_ws(u)
    key = world.key(ws, u)
    return {"user": u, "ws": ws, "agent": world.agent(key), "web": world.login(), "key": key}


def pts(values, start="2024-01-01"):
    import pandas as pd

    idx = pd.bdate_range(start, periods=len(values))
    return [[d.strftime("%Y-%m-%d"), float(v)] for d, v in zip(idx, values, strict=True)]
