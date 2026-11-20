import time

import numpy as np
import pandas as pd
import pytest

from portofion_engine.portfolio import (
    CycleError,
    Member,
    PortfolioNode,
    PortfolioSpec,
    erc_weights,
    evaluate,
    risk_contributions,
    simulate,
)
from portofion_engine.portfolio.schedule import scheduled_mask


def bser(vals, start="2024-01-01"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype="float64")


def rand(n, seed, mu=0.0003, sd=0.01, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    return bser(rng.normal(mu, sd, n), start)


def test_fixed_daily_5050_is_mean():
    a, b = rand(300, 1), rand(300, 2)
    res = simulate({"a": a, "b": b}, PortfolioSpec("fixed", "daily"), weights={"a": 0.5, "b": 0.5})
    np.testing.assert_allclose(res.returns.to_numpy(), (0.5 * a + 0.5 * b).to_numpy(), atol=1e-14)
    assert res.start == a.index[0] and res.asof == a.index[-1]


def test_buy_and_hold_nav_identity():
    a, b, c = rand(400, 1), rand(400, 2, sd=0.02), rand(400, 3, mu=-0.001)
    w = {"a": 0.2, "b": 0.5, "c": 0.3}
    res = simulate({"a": a, "b": b, "c": c}, PortfolioSpec("fixed", "none"), weights=w)
    nav_p = (1 + res.returns).cumprod()
    expect = sum(w[k] * (1 + s).cumprod() for k, s in {"a": a, "b": b, "c": c}.items())
    np.testing.assert_allclose(nav_p.to_numpy(), expect.to_numpy(), rtol=1e-12)
    assert len(res.rebalances) == 1


def test_monthly_rebalance_only_on_first_date_of_month():
    a, b = rand(300, 1), rand(300, 2)
    res = simulate({"a": a, "b": b}, PortfolioSpec("fixed", "monthly"), weights={"a": 0.5, "b": 0.5})
    months = [(d.year, d.month) for d in res.rebalances]
    assert len(months) == len(set(months))
    for d in res.rebalances[1:]:
        prev = res.returns.index[res.returns.index.get_loc(d) - 1]
        assert (prev.year, prev.month) != (d.year, d.month)
        np.testing.assert_allclose(res.weights.loc[d, ["a", "b"]].to_numpy(), [0.5, 0.5])
    off = res.returns.index[5]
    assert off not in res.rebalances
    assert abs(res.weights.loc[off, "a"] - 0.5) > 1e-6


def test_schedule_masks():
    idx = pd.bdate_range("2024-01-01", "2024-03-31")
    assert scheduled_mask(idx, "monthly").sum() == 3
    assert scheduled_mask(idx, "quarterly").sum() == 1
    assert scheduled_mask(idx, "weekly").sum() == len(set(idx.isocalendar().week))
    assert scheduled_mask(idx, "none").sum() == 0


@pytest.mark.parametrize("scheme", ["inverse_vol", "risk_parity"])
def test_no_lookahead(scheme):
    rs = {k: rand(400, i, sd=0.005 * (i + 1)) for i, k in enumerate("abc")}
    spec = PortfolioSpec(scheme, "weekly", lookback=40)
    base = simulate(rs, spec)
    t = 250
    cut = base.returns.index[t]
    bumped = {k: s.copy() for k, s in rs.items()}
    for s in bumped.values():
        s[s.index >= cut] = s[s.index >= cut] * 5 + 0.03
    alt = simulate(bumped, spec)
    pd.testing.assert_frame_equal(base.weights.loc[:cut], alt.weights.loc[:cut])


def test_erc_two_assets_equals_inverse_vol():
    cov = np.array([[0.04, 0.006], [0.006, 0.01]])
    w = erc_weights(cov)
    inv = 1 / np.sqrt(np.diag(cov))
    np.testing.assert_allclose(w, inv / inv.sum(), atol=1e-9)


def test_erc_diagonal_equals_inverse_vol_and_equal_rc():
    rng = np.random.default_rng(3)
    sd = rng.uniform(0.05, 0.4, 6)
    w = erc_weights(np.diag(sd**2))
    np.testing.assert_allclose(w, (1 / sd) / (1 / sd).sum(), atol=1e-9)
    X = rng.normal(size=(500, 8)) @ rng.normal(size=(8, 8))
    cov = np.cov(X, rowvar=False)
    w = erc_weights(cov)
    rc = risk_contributions(w, cov)
    assert rc.max() - rc.min() < 1e-8
    assert np.all(w > 0) and w.sum() == pytest.approx(1)


def test_entry_and_exit_trigger_rebalance():
    a = rand(200, 1)
    b = rand(100, 2, start="2020-03-02")
    res = simulate({"a": a, "b": b}, PortfolioSpec("equal", "none", start="earliest"))
    assert res.start == a.index[0]
    assert b.index[0] in res.rebalances
    assert res.weights.loc[b.index[0], "b"] == pytest.approx(0.5)
    after = a.index[a.index > b.index[-1]][0]
    assert after in res.rebalances
    assert res.weights.loc[after, "a"] == pytest.approx(1.0)
    assert res.weights.loc[after, "b"] == 0.0


def test_common_start():
    a = rand(200, 1)
    b = rand(100, 2, start="2020-03-02")
    res = simulate({"a": a, "b": b}, PortfolioSpec("equal", "none", stale_days=10000))
    assert res.start == b.index[0]


def test_fixed_renormalizes_over_alive():
    a = rand(200, 1)
    b = rand(100, 2, start="2020-03-02")
    res = simulate({"a": a, "b": b}, PortfolioSpec("fixed", "none", start="earliest"),
                   weights={"a": 0.6, "b": 0.4})
    assert res.weights["a"].iloc[0] == pytest.approx(1.0)
    assert res.weights.loc[b.index[0], ["a", "b"]].tolist() == pytest.approx([0.6, 0.4])
    res2 = simulate({"a": a, "b": b}, PortfolioSpec("fixed", "none", start="earliest", renormalize=False),
                    weights={"a": 0.6, "b": 0.4})
    assert res2.weights["a"].iloc[0] == pytest.approx(0.6)
    assert res2.weights["cash"].iloc[0] == pytest.approx(0.4)


def test_reported_weights_drift_cash_and_weekend():
    a, b = rand(60, 1), rand(60, 2)
    rep = pd.DataFrame({"a": [0.5, 0.3], "b": [0.3, 0.7]},
                       index=pd.to_datetime([a.index[0], "2020-01-18"]))  # Saturday
    res = simulate({"a": a, "b": b}, PortfolioSpec("reported"), reported=rep)
    assert res.start == a.index[0]
    assert res.weights["cash"].iloc[0] == pytest.approx(0.2)
    monday = pd.Timestamp("2020-01-20")
    assert monday in res.rebalances
    assert res.weights.loc[monday, ["a", "b"]].tolist() == pytest.approx([0.3, 0.7])
    mid = a.index[3]
    assert mid not in res.rebalances
    assert res.weights.loc[mid, "a"] != pytest.approx(0.5)
    r0 = 0.5 * a.iloc[0] + 0.3 * b.iloc[0]
    assert res.returns.iloc[0] == pytest.approx(r0)


def test_nested_equals_flat():
    a, b, x = rand(300, 1), rand(300, 2), rand(300, 3)
    daily = PortfolioSpec("fixed", "daily")
    nodes = {
        "child": PortfolioNode("child", daily, [Member("a", "leaf:a"), Member("b", "leaf:b")],
                               {"a": 0.5, "b": 0.5}),
        "parent": PortfolioNode("parent", daily, [Member("c", "pf:child"), Member("x", "leaf:x")],
                                {"c": 0.5, "x": 0.5}),
    }
    leaves = {"a": (a, 252.0), "b": (b, 252.0), "x": (x, 252.0)}
    out = evaluate("parent", nodes, leaves)
    flat = simulate({"a": a, "b": b, "x": x}, daily, weights={"a": 0.25, "b": 0.25, "x": 0.5})
    np.testing.assert_allclose(out["parent"].returns.to_numpy(), flat.returns.to_numpy(), atol=1e-14)
    assert "child" in out


def test_cycle_detected():
    spec = PortfolioSpec()
    nodes = {
        "A": PortfolioNode("A", spec, [Member("b", "pf:B")]),
        "B": PortfolioNode("B", spec, [Member("a", "pf:A")]),
    }
    with pytest.raises(CycleError) as ei:
        evaluate("A", nodes, {})
    assert ei.value.path == ["pf:A", "pf:B", "pf:A"]


def test_cost_equals_turnover_times_bps():
    a, b = rand(120, 1), rand(120, 2, sd=0.03)
    w = {"a": 0.5, "b": 0.5}
    free = simulate({"a": a, "b": b}, PortfolioSpec("fixed", "monthly"), weights=w)
    costly = simulate({"a": a, "b": b}, PortfolioSpec("fixed", "monthly", cost_bps=10), weights=w)
    d = free.rebalances[1]
    to = costly.turnover[d]
    assert to > 0
    # weights before the second rebalance are identical, so the gap is exactly the cost
    assert free.returns[d] - costly.returns[d] == pytest.approx(to * 10 / 1e4, rel=1e-6)


def test_lagging_member_moves_asof_and_exits():
    a = rand(300, 1)
    b = rand(295, 2)
    c = rand(250, 3)  # 50 business days behind -> lagging
    res = simulate({"a": a, "b": b, "c": c}, PortfolioSpec("equal", "monthly", stale_days=7))
    assert res.asof == b.index[-1]
    assert res.lagging == ["c"]
    assert res.weights["c"].iloc[-1] == 0.0


def test_empty_member_excluded():
    a = rand(50, 1)
    res = simulate({"a": a, "e": pd.Series(dtype="float64")}, PortfolioSpec("equal", "daily"))
    assert res.excluded == ["e"]
    assert res.weights["a"].iloc[0] == pytest.approx(1.0)


@pytest.mark.perf
def test_perf_risk_parity_50_members_20y():
    rng = np.random.default_rng(0)
    n, k = 5040, 50
    f = rng.normal(0, 0.01, (n, 3))
    X = f @ rng.normal(0, 1, (3, k)) * 0.3 + rng.normal(0, 0.01, (n, k))
    idx = pd.bdate_range("2005-01-03", periods=n)
    rs = {f"m{i}": pd.Series(X[:, i], index=idx) for i in range(k)}
    t0 = time.perf_counter()
    res = simulate(rs, PortfolioSpec("risk_parity", "monthly", lookback=126))
    assert time.perf_counter() - t0 < 2.0
    assert len(res.returns) > 4800
