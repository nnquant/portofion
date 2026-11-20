import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from portofion_client import Portofion, PortofionError, to_points
from portofion_engine.metrics import summary


@pytest.fixture
def sdk(setup, app):
    pf = Portofion("http://testserver", api_key=setup["key"], retries=0)
    pf._http = TestClient(app, headers={"Authorization": f"Bearer {setup['key']}"})
    return pf


def test_report_chunked_and_metrics_match_engine(sdk):
    idx = pd.bdate_range("2015-01-01", periods=2500)
    r = pd.Series(np.random.default_rng(3).normal(0.0004, 0.01, 2500), index=idx)
    r.iloc[10] = np.nan  # dropped client-side
    out = sdk.report("smoke", branch="main", version="v1", returns=r, params={"k": 1},
                     extras={"turnover": r.abs()}, scalars={"capacity_usd": 1e7}, chunk=1000)
    assert out["calls"] == 6  # 3 returns chunks + 3 turnover chunks
    assert out["totals"]["returns"]["inserted"] == 2499
    exp = summary(r.dropna())
    assert out["metrics"]["sharpe"] == pytest.approx(exp["sharpe"])
    assert sdk.last_date("smoke") == idx[-1].strftime("%Y-%m-%d")
    df = sdk.series("smoke/main@v1", ["returns"])
    assert len(df) == 2499


def test_nav_chunks_anchor_server_side(sdk):
    nav = pd.Series(100 * 1.001 ** np.arange(30), index=pd.bdate_range("2024-01-01", periods=30))
    out = sdk.report("navs", returns=nav, kind="nav", chunk=10)
    assert out["totals"]["returns"]["inserted"] == 29
    df = sdk.series("navs/main@v1", ["returns"])
    np.testing.assert_allclose(df["returns"].dropna().to_numpy(), 0.001, atol=1e-12)


def test_errors_raise(sdk):
    sdk.report("x", version={"label": "v1"}["label"], params={"a": 1}, returns={"2024-01-02": 0.01})
    with pytest.raises(PortofionError) as e:
        sdk.report("x", version="v1", params={"a": 2}, returns={"2024-01-03": 0.01})
    assert e.value.code == "VERSION_CONFLICT" and e.value.status == 409


def test_to_points():
    assert to_points({"2024-01-02": 1, "2024-01-01": None}) == [["2024-01-02", 1.0]]
    assert to_points([(pd.Timestamp("2024-01-01"), 0.5)]) == [["2024-01-01", 0.5]]
