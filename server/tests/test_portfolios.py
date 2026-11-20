import threading

import numpy as np
import pandas as pd
import pytest
from conftest import pts
from sqlalchemy import select

from portofion_engine.portfolio import PortfolioSpec, simulate
from portofion_server.db.models import Portfolio, PortfolioResultRow
from portofion_server.domain import portfolios as pf_mod


def rnd(n, seed, mu=0.0004, sd=0.01):
    return np.random.default_rng(seed).normal(mu, sd, n)


def push(agent, strategy, values, branch="main", version="v1", start="2023-01-02"):
    r = agent.post("/api/v1/ingest", json={"strategy": strategy, "branch": branch, "version": version,
                                           "returns": {"points": pts(values, start)}})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def two(setup):
    a, b = rnd(300, 1), rnd(300, 2, sd=0.02)
    push(setup["agent"], "alpha", a)
    push(setup["agent"], "beta", b)
    return setup, a, b


def base(setup):
    return f"/api/v1/w/{setup['ws'].slug}"


def test_fixed_portfolio_matches_engine(two):
    setup, a, b = two
    web = setup["web"]
    r = web.post(f"{base(setup)}/portfolios", json={
        "slug": "core", "spec": {"scheme": "fixed", "rebalance": "monthly"},
        "members": [{"ref": "alpha", "weight": 0.6}, {"ref": "beta/main@v1", "weight": 0.4}]})
    assert r.status_code == 200, r.text
    assert [m["key"] for m in r.json()["members"]] == ["alpha/main", "beta/main@v1"]
    res = web.get(f"{base(setup)}/portfolios/core/result").json()
    idx = pd.bdate_range("2023-01-02", periods=300)
    exp = simulate({"alpha/main": pd.Series(a, index=idx), "beta/main@v1": pd.Series(b, index=idx)},
                   PortfolioSpec("fixed", "monthly"), weights={"alpha/main": 0.6, "beta/main@v1": 0.4})
    np.testing.assert_allclose(res["returns"], exp.returns.to_numpy(), atol=1e-9)
    assert res["cached"] is False
    again = web.get(f"{base(setup)}/portfolios/core/result").json()
    assert again["cached"] is True and again["fingerprint"] == res["fingerprint"]
    lst = web.get(f"{base(setup)}/portfolios").json()
    assert lst[0]["slug"] == "core" and lst[0]["metrics"]["sharpe"] == pytest.approx(res["metrics"]["sharpe"])
    assert len(res["member_nav"]["series"]["alpha/main"]) == len(res["member_nav"]["dates"])


def test_cycle_rejected(two):
    setup, _, _ = two
    web = setup["web"]
    web.post(f"{base(setup)}/portfolios", json={"slug": "a", "members": [{"ref": "alpha"}]}).raise_for_status()
    web.post(f"{base(setup)}/portfolios", json={"slug": "b", "members": [{"ref": "pf:a"}]}).raise_for_status()
    r = web.put(f"{base(setup)}/portfolios/a", json={"slug": "a", "members": [{"ref": "pf:b"}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "PORTFOLIO_CYCLE"
    r = web.put(f"{base(setup)}/portfolios/a", json={"slug": "a", "members": [{"ref": "pf:a"}]})
    assert r.status_code == 422
    assert web.delete(f"{base(setup)}/portfolios/a").status_code == 409  # member of b


def test_nested_result(two):
    setup, a, b = two
    web = setup["web"]
    web.post(f"{base(setup)}/portfolios", json={
        "slug": "child", "spec": {"scheme": "fixed", "rebalance": "daily"},
        "members": [{"ref": "alpha", "weight": 0.5}, {"ref": "beta", "weight": 0.5}]}).raise_for_status()
    push(setup["agent"], "gamma", rnd(300, 3))
    web.post(f"{base(setup)}/portfolios", json={
        "slug": "parent", "spec": {"scheme": "fixed", "rebalance": "daily"},
        "members": [{"ref": "pf:child", "weight": 0.5}, {"ref": "gamma", "weight": 0.5}]}).raise_for_status()
    res = web.get(f"{base(setup)}/portfolios/parent/result").json()
    c = rnd(300, 3)
    np.testing.assert_allclose(res["returns"], 0.25 * a + 0.25 * b + 0.5 * c, atol=1e-9)
    # the child result was stored as a side effect
    assert web.get(f"{base(setup)}/portfolios/child/result").json()["cached"] is True


@pytest.fixture
def no_bg(monkeypatch):
    """Keep background refreshes from clearing the stale flag so it can be observed."""
    calls = []
    monkeypatch.setattr("portofion_server.api.v1.agent.refresh_portfolios", lambda ids: calls.append(ids))
    monkeypatch.setattr("portofion_server.api.v1.workspace.refresh_portfolios", lambda ids: calls.append(ids))
    return calls


def _stale(db, slug):
    db.expire_all()
    pid = db.scalar(select(Portfolio.id).where(Portfolio.slug == slug))
    return db.get(PortfolioResultRow, pid).stale


def test_invalidation(two, db, no_bg):
    setup, _, _ = two
    web = setup["web"]
    push(setup["agent"], "alpha", rnd(50, 5), version="v0")  # v0 becomes head of alpha/main
    web.patch(f"{base(setup)}/strategies/alpha/branches/main", json={"head": "v1"}).raise_for_status()
    web.post(f"{base(setup)}/portfolios", json={"slug": "follow", "members": [{"ref": "alpha"}, {"ref": "beta"}]}).raise_for_status()
    web.post(f"{base(setup)}/portfolios", json={"slug": "pinned", "members": [{"ref": "alpha/main@v1"}]}).raise_for_status()
    web.post(f"{base(setup)}/portfolios", json={"slug": "top", "members": [{"ref": "pf:follow"}]}).raise_for_status()
    for s in ("follow", "pinned", "top"):
        web.get(f"{base(setup)}/portfolios/{s}/result").raise_for_status()
        assert not _stale(db, s)
    # new data on a non-head version: nobody depends on it
    push(setup["agent"], "alpha", [0.01], version="v0", start="2024-06-03")
    assert not any(_stale(db, s) for s in ("follow", "pinned", "top"))
    # data on beta head -> follow and its parent go stale
    push(setup["agent"], "beta", [0.01], start="2024-03-01")
    assert _stale(db, "follow") and _stale(db, "top") and not _stale(db, "pinned")
    for s in ("follow", "top"):
        assert web.get(f"{base(setup)}/portfolios/{s}/result").json()["cached"] is False
    # head move on a followed branch -> follow stale; pinned member ignores it
    web.patch(f"{base(setup)}/strategies/alpha/branches/main", json={"head": "v0"}).raise_for_status()
    assert _stale(db, "follow") and not _stale(db, "pinned")
    res = web.get(f"{base(setup)}/portfolios/pinned/result").json()
    assert res["cached"] is True
    follow = web.get(f"{base(setup)}/portfolios/follow/result").json()
    assert follow["members"][0]["resolved"] == "alpha/main@v0"
    assert no_bg  # refreshes were scheduled


def test_fingerprint_catches_changes_without_stale_flag(two):
    setup, _, _ = two
    web = setup["web"]
    web.post(f"{base(setup)}/portfolios", json={"slug": "p", "members": [{"ref": "alpha"}]}).raise_for_status()
    r1 = web.get(f"{base(setup)}/portfolios/p/result").json()
    push(setup["agent"], "alpha", [0.02], start="2024-03-01")
    r2 = web.get(f"{base(setup)}/portfolios/p/result").json()
    assert r2["fingerprint"] != r1["fingerprint"] and r2["asof"] == "2024-03-01"


def test_reported_weights_via_agent(two):
    setup, a, b = two
    web = setup["web"]
    web.post(f"{base(setup)}/portfolios", json={"slug": "alloc", "spec": {"scheme": "reported"},
                                                "members": [{"ref": "alpha"}, {"ref": "beta"}]}).raise_for_status()
    r = setup["agent"].put("/api/v1/ingest/portfolio-weights", json={
        "portfolio": "pf:alloc",
        "reports": [{"date": "2023-01-02", "weights": {"alpha": 0.5, "beta/main": 0.3}},
                    {"date": "2023-06-03", "weights": {"alpha": 0.2, "beta": 0.8}}]})
    assert r.status_code == 200, r.text
    res = web.get(f"{base(setup)}/portfolios/alloc/result").json()
    assert res["returns"][0] == pytest.approx(0.5 * a[0] + 0.3 * b[0])
    assert "2023-06-05" in res["rebalances"]  # Saturday report applies Monday
    bad = setup["agent"].put("/api/v1/ingest/portfolio-weights", json={
        "portfolio": "alloc", "reports": [{"date": "2023-01-02", "weights": {"gamma": 1.0}}]})
    assert bad.status_code in (404, 422)


def test_preview_and_vol_schemes(two):
    setup, _, _ = two
    web = setup["web"]
    for scheme in ("equal", "inverse_vol", "risk_parity"):
        r = web.post(f"{base(setup)}/portfolios/preview", json={
            "spec": {"scheme": scheme, "rebalance": "weekly", "lookback": 40},
            "members": [{"ref": "alpha"}, {"ref": "beta"}]})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["metrics"]["n"] > 0
        if scheme != "equal":
            m = {x["key"]: x["current_weight"] for x in out["members"]}
            assert m["alpha/main"] > m["beta/main"]  # beta is twice as volatile


def test_concurrent_reads_compute_once(two, db, monkeypatch):
    setup, _, _ = two
    web = setup["web"]
    web.post(f"{base(setup)}/portfolios", json={"slug": "p", "members": [{"ref": "alpha"}, {"ref": "beta"}]}).raise_for_status()
    calls = []
    real = pf_mod._evaluate

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(pf_mod, "_evaluate", spy)
    results = []

    def go():
        results.append(web.get(f"{base(setup)}/portfolios/p/result").json()["fingerprint"])

    ts = [threading.Thread(target=go) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(results)) == 1 and len(calls) == 1
