import pytest
from conftest import pts

from portofion_server.auth.deps import Principal, WsCtx
from portofion_server.db.models import Membership
from portofion_server.domain import accounts


def test_login_logout_me(world, client):
    world.user()
    r = client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "nope"})
    assert r.status_code == 401
    web = world.login()
    me = web.get("/api/v1/auth/me").json()
    assert me["email"] == "alice@example.com"
    assert me["workspaces"][0]["kind"] == "personal" and me["workspaces"][0]["role"] == "admin"
    web.post("/api/v1/auth/logout")
    assert web.get("/api/v1/auth/me").status_code == 401


def test_login_throttle(world, client):
    world.user()
    for _ in range(10):
        client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "bad"})
    r = client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "password123"})
    assert r.status_code == 429


def test_signup_disabled_by_default(client):
    r = client.post("/api/v1/auth/signup", json={"email": "x@y.z", "password": "longenough"})
    assert r.status_code == 403


def test_bad_origin_rejected(setup):
    ws = setup["ws"].slug
    r = setup["web"].post(f"/api/v1/w/{ws}/keys", json={"name": "k"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "BAD_ORIGIN"


def _seed(setup):
    setup["agent"].post("/api/v1/ingest", json={"strategy": "alpha", "returns": {"points": pts([0.01] * 30)}}).raise_for_status()


@pytest.mark.parametrize("path", [
    "", "/library", "/strategies/alpha", "/versions/1/analytics", "/versions/1/series", "/compare?refs=alpha",
    "/portfolios", "/members", "/keys", "/flags",
])
def test_cross_workspace_is_404(setup, world, path):
    _seed(setup)
    world.user("bob@example.com")
    bob = world.login("bob@example.com")
    ws = setup["ws"].slug
    r = bob.get(f"/api/v1/w/{ws}{path}")
    assert r.status_code == 404, (path, r.text)
    # bob's key cannot read alice's workspace either
    bob_user = world.db.query(accounts.User).filter_by(email="bob@example.com").one()
    bob_key = world.key(world.personal_ws(bob_user), bob_user)
    assert world.agent(bob_key).get(f"/api/v1/w/{ws}{path}").status_code == 404
    # and alice can
    assert setup["web"].get(f"/api/v1/w/{ws}{path}").status_code == 200, path


def test_roles(setup, world, db):
    _seed(setup)
    ws = setup["ws"]
    bob = world.user("bob@example.com")
    db.add(Membership(workspace_id=ws.id, user_id=bob.id, role="viewer"))
    db.commit()
    web_bob = world.login("bob@example.com")
    base = f"/api/v1/w/{ws.slug}"
    assert web_bob.get(f"{base}/library").status_code == 200
    assert web_bob.patch(f"{base}/strategies/alpha", json={"name": "A"}).status_code == 403
    assert web_bob.post(f"{base}/keys", json={"name": "k"}).status_code == 403
    m = db.get(Membership, (ws.id, bob.id))
    m.role = "member"
    db.commit()
    assert web_bob.patch(f"{base}/strategies/alpha", json={"name": "A"}).status_code == 200
    assert web_bob.post(f"{base}/keys", json={"name": "k"}).status_code == 200
    assert web_bob.delete(f"{base}/strategies/alpha").status_code == 403
    assert web_bob.patch(f"{base}/settings", json={"rf": 0.02}).status_code == 403
    assert web_bob.get(f"{base}/audit").status_code == 403
    # API keys are read-only for UI endpoints
    assert setup["agent"].patch(f"{base}/strategies/alpha", json={"name": "B"}).status_code == 403
    assert setup["web"].delete(f"{base}/strategies/alpha").status_code == 200


def test_invites(setup, world, client):
    ws = setup["ws"]
    inv = setup["web"].post(f"/api/v1/w/{ws.slug}/invites", json={"role": "member"}).json()
    token = inv["url"].rsplit("/", 1)[1]
    assert client.get(f"/api/v1/invites/{token}").json()["role"] == "member"
    r = client.post(f"/api/v1/invites/{token}/accept",
                    json={"email": "carol@example.com", "password": "password123", "name": "Carol"})
    assert r.status_code == 200 and r.json()["workspace"] == ws.slug
    me = client.get("/api/v1/auth/me").json()
    assert {w["slug"] for w in me["workspaces"]} >= {ws.slug}
    assert client.post(f"/api/v1/invites/{token}/accept", json={}).status_code == 404  # single-use


def test_last_admin_protected(setup, world, db):
    ctx = WsCtx(setup["ws"], "admin", Principal(user=setup["user"]))
    team = accounts.create_workspace(db, setup["user"], "team-a", "Team A")
    tctx = WsCtx(team, "admin", Principal(user=setup["user"]))
    from portofion_server.errors import ApiError

    with pytest.raises(ApiError) as e:
        accounts.set_role(db, tctx, setup["user"].id, "member")
    assert e.value.code == "LAST_ADMIN"
    with pytest.raises(ApiError):
        accounts.remove_member(db, ctx, setup["user"].id)  # personal workspace


def test_revoked_key(setup, world, db):
    keys = setup["web"].get(f"/api/v1/w/{setup['ws'].slug}/keys").json()
    setup["web"].delete(f"/api/v1/w/{setup['ws'].slug}/keys/{keys[0]['id']}").raise_for_status()
    assert setup["agent"].get("/api/v1/agent/whoami").status_code == 401


def test_migrations_roundtrip():
    import psycopg
    from conftest import DEV_ADMIN

    from portofion_server.db.migrate import downgrade, upgrade

    try:
        with psycopg.connect(DEV_ADMIN, autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS portofion_test_mig")
            c.execute("CREATE DATABASE portofion_test_mig")
    except psycopg.OperationalError:
        pytest.skip("dev database not reachable")
    url = "postgresql+psycopg://portofion:portofion@127.0.0.1:55432/portofion_test_mig"
    upgrade(url=url)
    downgrade(url=url)
    upgrade(url=url)
    with psycopg.connect(DEV_ADMIN, autocommit=True) as c:
        c.execute("DROP DATABASE portofion_test_mig WITH (FORCE)")


def test_forwarded_host_origin_allowed(setup):
    ws = setup["ws"].slug
    r = setup["web"].post(f"/api/v1/w/{ws}/keys", json={"name": "k"},
                          headers={"Origin": "http://localhost:5173", "X-Forwarded-Host": "localhost:5173"})
    assert r.status_code == 200, r.text
