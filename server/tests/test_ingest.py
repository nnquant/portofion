import gzip
import json
import math
import threading

import numpy as np
import pytest
from conftest import pts
from sqlalchemy import func, select

from portofion_server.db.models import Branch, SeriesRevision, Strategy, Version, VersionMetrics


def body(values=(0.01, -0.02, 0.03, 0.005, 0.0), **kw):
    b = {"strategy": "alpha", "branch": "main", "version": "v1", "returns": {"points": pts(values)}}
    b.update(kw)
    return b


def test_ingest_creates_everything_and_is_idempotent(setup):
    agent = setup["agent"]
    r = agent.post("/api/v1/ingest", json=body())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["strategy"]["created"] and out["branch"]["created"] and out["version"]["created"]
    assert out["version"]["is_head"]
    s = out["series"]["returns"]
    assert (s["inserted"], s["updated"], s["unchanged"], s["n_points"]) == (5, 0, 0, 5)
    assert out["metrics"]["n"] == 5 and out["metrics"]["sharpe"] is not None
    r2 = agent.post("/api/v1/ingest", json=body()).json()
    assert not r2["strategy"]["created"] and not r2["version"]["created"]
    s2 = r2["series"]["returns"]
    assert (s2["inserted"], s2["unchanged"]) == (0, 5)


def test_metrics_cached_match_engine(setup, db):
    from portofion_engine.metrics import summary

    vals = np.random.default_rng(0).normal(0.0005, 0.01, 300)
    setup["agent"].post("/api/v1/ingest", json=body(vals)).raise_for_status()
    m = db.scalar(select(VersionMetrics).where(VersionMetrics.window == "all"))
    import pandas as pd

    expect = summary(pd.Series(vals, index=pd.bdate_range("2024-01-01", periods=300)), 252)
    assert m.n == 300
    assert m.sharpe == pytest.approx(expect["sharpe"])
    assert m.max_dd == pytest.approx(expect["max_dd"])


def test_version_conflict_and_fingerprint_reuse(setup):
    agent = setup["agent"]
    agent.post("/api/v1/ingest", json=body(version={"label": "v1", "params": {"a": 1}})).raise_for_status()
    r = agent.post("/api/v1/ingest", json=body(version={"label": "v1", "params": {"a": 2}}))
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    assert r.json()["error"]["details"]["params"]["stored"] == {"a": 1}
    # no label + same params -> same version; different params -> new auto label
    same = agent.post("/api/v1/ingest", json=body(version={"params": {"a": 1}})).json()
    assert same["version"]["label"] == "v1" and not same["version"]["created"]
    new = agent.post("/api/v1/ingest", json=body(version={"params": {"a": 3}})).json()
    assert new["version"]["label"] == "v2" and new["version"]["created"] and new["version"]["is_head"]


def test_head_lock(setup, db):
    agent, web = setup["agent"], setup["web"]
    agent.post("/api/v1/ingest", json=body()).raise_for_status()
    ws = setup["ws"].slug
    web.patch(f"/api/v1/w/{ws}/strategies/alpha/branches/main", json={"head_locked": True}).raise_for_status()
    r = agent.post("/api/v1/ingest", json=body(version="v2")).json()
    assert r["version"]["created"] and not r["version"]["is_head"]
    assert any(w["code"] == "HEAD_LOCKED" for w in r["warnings"])


def test_default_version_is_head_or_v1(setup):
    agent = setup["agent"]
    r = agent.post("/api/v1/ingest", json={"strategy": "beta", "returns": {"points": pts([0.01, 0.02])}}).json()
    assert r["branch"]["name"] == "main" and r["version"]["label"] == "v1"
    r = agent.post("/api/v1/ingest", json={"strategy": "beta", "returns": {"points": pts([0.03], "2024-02-01")}}).json()
    assert r["version"]["label"] == "v1" and r["series"]["returns"]["n_points"] == 3


def test_nav_anchor_and_stored_anchor(setup):
    agent = setup["agent"]
    r = agent.post("/api/v1/ingest", json=body(returns={"kind": "nav", "points": pts([100, 101, 102.01])})).json()
    s = r["series"]["returns"]
    assert s["inserted"] == 2 and s["anchor_dropped"] == "2024-01-01"
    r = agent.post("/api/v1/ingest", json=body(returns={"kind": "nav", "points": [["2024-01-04", 103.0301]]})).json()
    assert r["series"]["returns"]["inserted"] == 1
    data = agent.get("/api/v1/agent/series", params={"ref": "alpha/main@v1", "names": "returns,nav"}).json()
    assert data["series"]["returns"][0] is None  # the anchor day has a NAV but no return
    np.testing.assert_allclose(data["series"]["returns"][1:], [0.01, 0.01, 0.01], atol=1e-12)
    assert data["series"]["nav"][-1] == pytest.approx(103.0301)
    r = agent.post("/api/v1/ingest", json=body(version="v9", returns={"kind": "nav", "base_nav": 100,
                                                                      "points": pts([101])})).json()
    assert r["series"]["returns"]["inserted"] == 1


def test_log_returns(setup):
    r = setup["agent"].post("/api/v1/ingest", json=body(returns={"kind": "log_return",
                                                                 "points": pts([math.log(1.02)])}))
    data = setup["agent"].get("/api/v1/agent/series", params={"ref": "alpha/main@v1"}).json()
    assert r.status_code == 200 and data["series"]["returns"][0] == pytest.approx(0.02)


def test_correction_creates_revision_and_bumps_rev(setup, db):
    agent = setup["agent"]
    agent.post("/api/v1/ingest", json=body()).raise_for_status()
    st = agent.get("/api/v1/agent/status", params={"strategy": "alpha"}).json()
    rev0 = st["strategies"][0]["branches"][0]["versions"][0]["series"]["returns"]["data_rev"]
    fixed = pts([0.01, -0.015, 0.03, 0.005, 0.0])
    r = agent.post("/api/v1/ingest", json=body(returns={"points": fixed})).json()
    assert r["series"]["returns"]["updated"] == 1
    revs = db.scalars(select(SeriesRevision)).all()
    assert len(revs) == 1 and revs[0].old_value == -0.02 and revs[0].new_value == -0.015
    st = agent.get("/api/v1/agent/status", params={"strategy": "alpha"}).json()
    assert st["strategies"][0]["branches"][0]["versions"][0]["series"]["returns"]["data_rev"] == rev0 + 1
    # skip / error modes
    r = agent.post("/api/v1/ingest", json=body(on_existing="skip")).json()
    assert r["series"]["returns"]["skipped"] == 1
    r = agent.post("/api/v1/ingest", json=body(on_existing="error"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "POINT_CONFLICT"


def test_replace_and_delete(setup):
    agent = setup["agent"]
    agent.post("/api/v1/ingest", json=body()).raise_for_status()
    r = agent.post("/api/v1/ingest", json=body(returns={"points": pts([0.01, -0.02]), "replace": True})).json()
    assert r["series"]["returns"]["deleted"] == 3 and r["series"]["returns"]["n_points"] == 2
    r = agent.post("/api/v1/ingest", json=body(returns={"delete": ["2024-01-01"]})).json()
    assert r["series"]["returns"]["deleted"] == 1 and r["series"]["returns"]["n_points"] == 1


def test_warnings_outlier_and_percent_units(setup):
    agent = setup["agent"]
    r = agent.post("/api/v1/ingest", json=body([0.01, 0.62, 0.0, 0.01, 0.02])).json()
    assert [w["code"] for w in r["warnings"]] == ["OUTLIER"]
    r = agent.post("/api/v1/ingest", json=body([1.2, -0.8, 2.5, 0.3, -1.1 + 0.2], version="pct")).json()
    assert any(w["code"] == "PERCENT_UNITS" for w in r["warnings"])
    r = agent.post("/api/v1/ingest", json=body([0.01, 0.62, 0.0, 0.01, 0.02], version="strict", strict=True))
    assert r.status_code == 422
    flags = setup["web"].get(f"/api/v1/w/{setup['ws'].slug}/flags").json()
    assert {f["code"] for f in flags} >= {"OUTLIER", "PERCENT_UNITS"}


@pytest.mark.parametrize("points,code", [
    ([["2024-01-01", None]], "NULL_VALUE"),
    ([["2024/01/01", 0.01]], "BAD_DATE"),
    ([["2024-01-01T00:00:00", 0.01]], "BAD_DATE"),
    ([["2024-01-01", 0.01], ["2024-01-01", 0.02]], "DUPLICATE_DATE"),
    ([["2999-01-01", 0.01]], "DATE_OUT_OF_RANGE"),
    ([["2024-01-01", -1.0]], "RETURN_BELOW_MINUS_ONE"),
    ([["2024-01-01", "x"]], "BAD_VALUE"),
])
def test_hard_errors_write_nothing(setup, db, points, code):
    r = setup["agent"].post("/api/v1/ingest", json=body(returns={"points": points}))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"][0]["code"] == code
    assert db.scalar(select(func.count()).select_from(Strategy)) == 0


def test_nan_rejected(setup, db):
    raw = json.dumps(body(returns={"points": [["2024-01-01", float("nan")]]}))
    r = setup["agent"].post("/api/v1/ingest", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert db.scalar(select(func.count()).select_from(Strategy)) == 0


def test_limits(setup, monkeypatch):
    from portofion_server.settings import get_settings

    monkeypatch.setattr(get_settings(), "max_points_per_series", 3)
    r = setup["agent"].post("/api/v1/ingest", json=body())
    assert r.status_code == 413 and r.json()["error"]["code"] == "BATCH_TOO_LARGE"


def test_idempotency(setup):
    agent = setup["agent"]
    h = {"Idempotency-Key": "abc-1"}
    r1 = agent.post("/api/v1/ingest", json=body(), headers=h).json()
    r2 = agent.post("/api/v1/ingest", json=body(), headers=h).json()
    assert r2["replayed"] and r2["batch_id"] == r1["batch_id"]
    r3 = agent.post("/api/v1/ingest", json=body([0.5]), headers=h)
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_MISMATCH"


def test_dry_run_writes_nothing(setup, db):
    r = setup["agent"].post("/api/v1/ingest", json=body(dry_run=True)).json()
    assert r["dry_run"] and r["series"]["returns"]["inserted"] == 5
    assert db.scalar(select(func.count()).select_from(Strategy)) == 0


def test_gzip_body(setup):
    raw = gzip.compress(json.dumps(body()).encode())
    r = setup["agent"].post("/api/v1/ingest", content=raw,
                            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"})
    assert r.status_code == 200, r.text


def test_extras_and_scalars(setup):
    agent = setup["agent"]
    r = agent.post("/api/v1/ingest", json=body(series={"turnover": {"points": {"2024-01-01": 0.2}}},
                                               scalars={"capacity_usd": 5e7, "hold": {"value": 3.2, "as_of": "2024-01-05"}})).json()
    assert r["series"]["turnover"]["inserted"] == 1 and r["scalars"]["set"] == ["capacity_usd", "hold"]
    bad = agent.post("/api/v1/ingest", json=body(series={"Bad-Name": {"points": {}}}))
    assert bad.status_code == 422
    bad = agent.post("/api/v1/ingest", json=body(series={"returns": {"points": {}}}))
    assert bad.status_code == 422


def test_batch_partial_failure(setup):
    items = [body(), body(returns={"points": [["bad", 1]]}), body(strategy="gamma")]
    r = setup["agent"].post("/api/v1/ingest/batch", json={"items": items}).json()
    assert r["n_ok"] == 2 and r["n_failed"] == 1
    assert r["results"][1]["error"]["code"] == "VALIDATION_ERROR"
    st = setup["agent"].get("/api/v1/agent/status").json()
    assert {s["slug"] for s in st["strategies"]} == {"alpha", "gamma"}


def test_concurrent_creation_is_safe(setup, db):
    errors = []

    def go(i):
        try:
            r = setup["agent"].post("/api/v1/ingest", json=body(strategy="race", version="v1"))
            if r.status_code != 200:
                errors.append(r.text)
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert db.scalar(select(func.count()).select_from(Version)) == 1
    assert db.scalar(select(func.count()).select_from(Branch)) == 1


def test_key_scopes_and_auth(setup, world):
    r = world.agent("pfn_badbadba_nope").post("/api/v1/ingest", json=body())
    assert r.status_code == 401
    read_only = world.key(setup["ws"], setup["user"], scopes=("read",))
    r = world.agent(read_only).post("/api/v1/ingest", json=body())
    assert r.status_code == 403
    assert world.agent(read_only).get("/api/v1/agent/whoami").json()["scopes"] == ["read"]


def test_llms_txt(client):
    r = client.get("/llms.txt")
    assert r.status_code == 200 and "POST /api/v1/ingest" in r.text and "http://testserver" in r.text
