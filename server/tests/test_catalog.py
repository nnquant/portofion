import numpy as np
import pandas as pd
import pytest
from conftest import pts

from portofion_engine.metrics import summary


def _push(agent, strategy, values, branch="main", version="v1", start="2023-01-02", **kw):
    b = {"strategy": strategy, "branch": branch, "version": version,
         "returns": {"points": pts(values, start)}, **kw}
    r = agent.post("/api/v1/ingest", json=b)
    assert r.status_code == 200, r.text
    return r.json()


def rnd(n, seed, mu=0.0004, sd=0.01):
    return np.random.default_rng(seed).normal(mu, sd, n)


def test_library_rows_and_metrics(setup):
    a = rnd(400, 1)
    _push(setup["agent"], "alpha", a)
    _push(setup["agent"], "alpha", rnd(200, 2), version="v2", start="2023-06-01")
    _push(setup["agent"], "alpha", rnd(300, 3), branch="lowto")
    _push(setup["agent"], {"slug": "bench", "kind": "benchmark"}, rnd(400, 9))
    ws = setup["ws"].slug
    lib = setup["web"].get(f"/api/v1/w/{ws}/library").json()
    assert {(r["strategy"], r["branch"], r["version"]) for r in lib["rows"]} == {
        ("alpha", "main", "v2"), ("alpha", "lowto", "v1"), ("bench", "main", "v1")}
    lv = setup["web"].get(f"/api/v1/w/{ws}/library?level=version").json()
    assert len(lv["rows"]) == 4
    row = next(r for r in lv["rows"] if r["version"] == "v1" and r["branch"] == "main")
    exp = summary(pd.Series(a, index=pd.bdate_range("2023-01-02", periods=400)))
    assert row["m"]["sharpe"] == pytest.approx(exp["sharpe"])
    assert len(row["spark"]) == 64 and row["n_versions"] == 2 and not row["is_head"]


def test_strategy_detail_and_patches(setup):
    _push(setup["agent"], "alpha", rnd(100, 1), version={"label": "v1", "params": {"k": 1}})
    _push(setup["agent"], "alpha", rnd(100, 2), version={"label": "v2", "params": {"k": 2}})
    ws = setup["ws"].slug
    web = setup["web"]
    d = web.get(f"/api/v1/w/{ws}/strategies/alpha").json()
    br = d["branches"][0]
    assert [v["label"] for v in br["versions"]] == ["v2", "v1"]
    v1 = br["versions"][1]
    assert br["head_version_id"] == br["versions"][0]["id"] and v1["params"] == {"k": 1}
    d = web.patch(f"/api/v1/w/{ws}/strategies/alpha/branches/main", json={"head": "v1"}).json()
    assert d["branches"][0]["head_version_id"] == v1["id"]
    d = web.patch(f"/api/v1/w/{ws}/strategies/alpha", json={"tags": ["cn", "momo"], "periods_per_year": 244}).json()
    assert d["strategy"]["tags"] == ["cn", "momo"] and d["strategy"]["effective_periods_per_year"] == 244
    assert web.patch(f"/api/v1/w/{ws}/versions/{v1['id']}", json={"stage": "live", "live_since": "2023-03-01"}).status_code == 200
    lib = web.get(f"/api/v1/w/{ws}/library?window=live&level=version").json()
    live = next(r for r in lib["rows"] if r["version"] == "v1")
    assert live["stage"] == "live" and live["m"]["first_date"] >= "2023-03-01"
    audit = web.get(f"/api/v1/w/{ws}/audit").json()
    assert {"branch.head", "strategy.update", "version.update"} <= {a["action"] for a in audit}


def test_analytics_etag_and_benchmark(setup):
    a = rnd(300, 1)
    _push(setup["agent"], "alpha", a)
    _push(setup["agent"], "bench", rnd(300, 2))
    ws = setup["ws"].slug
    web = setup["web"]
    vid = web.get(f"/api/v1/w/{ws}/library").json()["rows"][0]["version_id"]
    r = web.get(f"/api/v1/w/{ws}/versions/{vid}/analytics", params={"benchmark": "bench", "rolling": 63})
    out = r.json()
    assert len(out["nav"]) == 300 and out["relative"]["n_overlap"] == 300
    assert out["metrics"]["sharpe"] == pytest.approx(
        summary(pd.Series(a, index=pd.bdate_range("2023-01-02", periods=300)))["sharpe"])
    r2 = web.get(f"/api/v1/w/{ws}/versions/{vid}/analytics", params={"benchmark": "bench", "rolling": 63},
                 headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
    csv = web.get(f"/api/v1/w/{ws}/versions/{vid}/series", params={"format": "csv"}).text
    assert csv.splitlines()[0] == "date,returns" and len(csv.splitlines()) == 301


def test_compare(setup):
    _push(setup["agent"], "alpha", rnd(300, 1))
    _push(setup["agent"], "beta", rnd(200, 2), start="2023-04-03")
    ws = setup["ws"].slug
    out = setup["web"].get(f"/api/v1/w/{ws}/compare", params={"refs": "alpha,beta/main@v1", "corr_freq": "D"}).json()
    assert out["keys"] == ["alpha", "beta/main@v1"]
    assert out["common_start"] == "2023-04-03"
    n = out["corr"]["n"][0][1]
    assert n == 200
    assert out["metrics_own"]["alpha"]["n"] == 300 and out["metrics_common"]["alpha"]["n"] == 200
    bad = setup["web"].get(f"/api/v1/w/{ws}/compare", params={"refs": "nope"})
    assert bad.status_code == 404


def test_revisions_flags_batches(setup):
    _push(setup["agent"], "alpha", [0.01, 0.7, 0.02])
    _push(setup["agent"], "alpha", [0.01, 0.07, 0.02])
    ws = setup["ws"].slug
    web = setup["web"]
    vid = web.get(f"/api/v1/w/{ws}/library").json()["rows"][0]["version_id"]
    revs = web.get(f"/api/v1/w/{ws}/versions/{vid}/revisions").json()
    assert revs[0]["old"] == 0.7 and revs[0]["new"] == 0.07
    # the correction cleared the outlier flag
    assert web.get(f"/api/v1/w/{ws}/versions/{vid}/flags").json() == []
    _push(setup["agent"], "alpha", [0.9], start="2023-01-05")
    assert web.get(f"/api/v1/w/{ws}/flags").json()[0]["code"] == "OUTLIER"
    assert web.post(f"/api/v1/w/{ws}/flags/ack", json={"version_id": vid}).json()["acked"] == 1
    assert web.get(f"/api/v1/w/{ws}/flags").json() == []
    b = web.get(f"/api/v1/w/{ws}/versions/{vid}/batches").json()
    assert len(b) == 3 and b[0]["key"] == "test-key"


def test_search_and_delete_version(setup):
    _push(setup["agent"], "alpha", rnd(50, 1))
    _push(setup["agent"], "alpha", rnd(50, 2), version="v2")
    ws = setup["ws"].slug
    web = setup["web"]
    hits = web.get(f"/api/v1/w/{ws}/search", params={"q": "alp"}).json()
    assert {"alpha", "alpha/main", "alpha/main@v2"} <= {h["ref"] for h in hits}
    d = web.get(f"/api/v1/w/{ws}/strategies/alpha").json()
    v2 = d["branches"][0]["versions"][0]
    assert web.delete(f"/api/v1/w/{ws}/versions/{v2['id']}").status_code == 200
    d = web.get(f"/api/v1/w/{ws}/strategies/alpha").json()
    assert d["branches"][0]["head_version_id"] == d["branches"][0]["versions"][0]["id"]


def test_periods_per_year_changes_metrics(setup):
    _push(setup["agent"], "alpha", rnd(300, 1))
    ws = setup["ws"].slug
    web = setup["web"]
    s0 = web.get(f"/api/v1/w/{ws}/library").json()["rows"][0]["m"]["sharpe"]
    web.patch(f"/api/v1/w/{ws}/strategies/alpha", json={"periods_per_year": 63}).raise_for_status()
    s1 = web.get(f"/api/v1/w/{ws}/library").json()["rows"][0]["m"]["sharpe"]
    assert s1 == pytest.approx(s0 * np.sqrt(63 / 252))


def test_tag_colors_distinct_and_stable(setup):
    from portofion_server.domain.common import TAG_COLORS

    _push(setup["agent"], {"slug": "alpha", "tags": ["cn", "momo", "prod"]}, rnd(50, 1))
    _push(setup["agent"], {"slug": "beta", "tags": ["cn", "trend"]}, rnd(50, 2))
    ws, web = setup["ws"].slug, setup["web"]
    colors = web.get(f"/api/v1/w/{ws}/library").json()["tag_colors"]
    assert set(colors) == {"cn", "momo", "prod", "trend"}
    assert all(c in TAG_COLORS for c in colors.values())
    assert len(set(colors.values())) == 4  # fewer tags than colors: all distinct
    # a new tag on an existing strategy avoids that strategy's colors and leaves the others unchanged
    d = web.patch(f"/api/v1/w/{ws}/strategies/beta", json={"tags": ["cn", "trend", "us"]}).json()
    tc = d["strategy"]["tag_colors"]
    assert tc["cn"] == colors["cn"] and tc["trend"] == colors["trend"]
    assert tc["us"] not in {colors["cn"], colors["trend"]}
    assert web.get(f"/api/v1/w/{ws}/library").json()["tag_colors"] == {**colors, "us": tc["us"]}
