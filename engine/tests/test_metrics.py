import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from portofion_engine.calendar import align, as_returns, resample_compound, window_slice
from portofion_engine.drawdown import drawdown_series, episodes
from portofion_engine.metrics import summary
from portofion_engine.relative import corr_matrix, relative
from portofion_engine.sparkline import lttb, sparkline
from portofion_engine.tables import monthly_table


def s(vals, start="2024-01-01", freq="B"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)) if freq == "B"
                     else pd.date_range(start, periods=len(vals), freq=freq), dtype="float64")


def test_hand_computed_basic():
    r = s([0.01, -0.02, 0.03])
    m = summary(r, periods_per_year=252)
    mean = (0.01 - 0.02 + 0.03) / 3
    sd = np.std([0.01, -0.02, 0.03], ddof=1)
    assert m["n"] == 3
    assert m["ann_ret"] == pytest.approx(mean * 252)
    assert m["ann_vol"] == pytest.approx(sd * math.sqrt(252))
    assert m["sharpe"] == pytest.approx(mean / sd * math.sqrt(252))
    nav_end = 1.01 * 0.98 * 1.03
    assert m["total_ret"] == pytest.approx(nav_end - 1)
    assert m["cagr"] == pytest.approx(nav_end ** (252 / 3) - 1)
    # drawdown: peak 1.01 -> 0.9898
    assert m["max_dd"] == pytest.approx(0.98 - 1)
    assert m["calmar"] == pytest.approx(mean * 252 / 0.02)
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["profit_factor"] == pytest.approx(0.04 / 0.02)
    downside = math.sqrt((0.02**2) / 3)
    assert m["sortino"] == pytest.approx(mean / downside * math.sqrt(252))
    assert m["best"] == 0.03 and m["worst"] == -0.02


def test_first_day_loss_counts_as_drawdown():
    r = s([-0.05, 0.01, 0.01])
    m = summary(r)
    assert m["max_dd"] == pytest.approx(-0.05)


def test_undefined_metrics_are_none():
    assert summary(s([]))["sharpe"] is None
    m = summary(s([0.01]))
    assert m["sharpe"] is None and m["ann_vol"] is None and m["n"] == 1
    flat = summary(s([0.0] * 10))
    assert flat["sharpe"] is None and flat["calmar"] is None and flat["win_rate"] is None
    assert flat["max_dd"] == 0.0


def test_var_cvar():
    vals = np.linspace(-0.05, 0.05, 101)
    m = summary(s(vals), var_level=0.95)
    q = np.quantile(vals, 0.05)
    assert m["var"] == pytest.approx(q)
    assert m["cvar"] == pytest.approx(vals[vals <= q].mean())


def test_drawdown_episodes_and_duration():
    idx = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-10", "2024-01-20"])
    r = pd.Series([0.10, -0.10, -0.05, 0.20, -0.01], index=idx)
    eps = episodes(r)
    assert len(eps) == 2
    first = eps[0]
    assert first.peak == pd.Timestamp("2024-01-01")
    assert first.trough == pd.Timestamp("2024-01-05")
    assert first.recovery == pd.Timestamp("2024-01-10")
    assert first.days == 9
    assert eps[1].recovery is None and eps[1].days == 10
    m = summary(r)
    assert m["max_dd_days"] == 9
    assert m["cur_dd_days"] == 10
    assert m["cur_dd"] == pytest.approx(-0.01)


def test_alignment_fill_rules():
    a = pd.Series([0.01, 0.02, 0.03], index=pd.to_datetime(["2024-01-01", "2024-01-03", "2024-01-04"]))
    b = pd.Series([0.05, 0.06], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    al, obs = align({"a": a, "b": b})
    assert list(al.index) == list(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]))
    assert al["a"].tolist() == [0.01, 0.0, 0.02, 0.03]  # 01-02 inside life, unobserved -> 0
    assert np.isnan(al["b"].iloc[0]) and np.isnan(al["b"].iloc[3])  # outside life -> NaN
    assert obs["a"].tolist() == [True, False, True, True]


def test_cn_us_crypto_calendars_metrics_use_own_observations():
    crypto = s(np.full(365, 0.001), start="2024-01-01", freq="D")
    cn = s(np.full(240, 0.001), start="2024-01-02")
    m_c = summary(crypto, periods_per_year=365)
    m_n = summary(cn, periods_per_year=252)
    assert m_c["n"] == 365 and m_n["n"] == 240
    al, _ = align({"c": crypto, "n": cn})
    # weekends for cn inside its life are zero-filled in the aligned view only
    assert (al["n"].dropna() == 0).sum() > 0


def test_window_slice():
    r = s(np.full(800, 0.001), start="2022-01-03")
    last = r.index[-1]
    assert window_slice(r, "ytd").index[0].year == last.year
    one = window_slice(r, "1y")
    assert one.index[0] > last - pd.DateOffset(years=1)
    live = window_slice(r, "live", live_since=r.index[100])
    assert live.index[0] == r.index[100]
    assert window_slice(r, "live").empty


def test_monthly_table_compounds():
    r = s([0.01] * 3, start="2024-01-30")  # Tue 30, Wed 31, Thu Feb 1
    t = monthly_table(r)
    assert t["years"] == [2024]
    assert t["rows"][0][0] == pytest.approx(1.01**2 - 1)
    assert t["rows"][0][1] == pytest.approx(0.01)
    assert t["rows"][0][12] == pytest.approx(1.01**3 - 1)


def test_relative_and_corr():
    rng = np.random.default_rng(0)
    b = s(rng.normal(0, 0.01, 500))
    a = 0.5 * b + s(rng.normal(0, 0.005, 500))
    rel = relative(a, b)
    assert rel["beta"] == pytest.approx(0.5, abs=0.05)
    assert rel["n_overlap"] == 500
    short = relative(a.iloc[:10], b)
    assert short["beta"] is None
    cm = corr_matrix({"a": a, "b": b})
    assert cm["corr"][0][0] == 1.0
    assert cm["corr"][0][1] == pytest.approx(a.corr(b))
    assert cm["n"][0][1] == 500
    cmm = corr_matrix({"a": a, "b": b}, freq="M")
    assert cmm["n"][0][1] == len(resample_compound(a, "M"))


def test_sparkline_lttb():
    y = np.sin(np.linspace(0, 10, 1000))
    idx = lttb(y, 64)
    assert len(idx) == 64 and idx[0] == 0 and idx[-1] == 999
    assert np.all(np.diff(idx) > 0)
    sp = sparkline(s(np.full(500, 0.001)), 64)
    assert len(sp) == 64 and sp[0] == 1.0


def test_as_returns_normalizes():
    r = as_returns({"2024-01-03": 0.01, "2024-01-02": np.nan, "2024-01-01": 0.02})
    assert list(r.index) == list(pd.to_datetime(["2024-01-01", "2024-01-03"]))


finite_returns = st.lists(st.floats(-0.2, 0.2, allow_nan=False), min_size=5, max_size=200)


@given(finite_returns, st.floats(0.1, 10))
@settings(max_examples=60, deadline=None)
def test_sharpe_scale_invariant(vals, k):
    r = s(vals)
    a, b = summary(r)["sharpe"], summary(r * k)["sharpe"]
    if a is None:
        return
    assert b == pytest.approx(a, rel=1e-6, abs=1e-9)


@given(finite_returns)
@settings(max_examples=60, deadline=None)
def test_max_dd_nonpositive_and_matches_series(vals):
    r = s(vals)
    m = summary(r)
    assert m["max_dd"] <= 0
    assert m["max_dd"] == pytest.approx(min(drawdown_series(r).min(), 0.0))


@given(finite_returns)
@settings(max_examples=60, deadline=None)
def test_nav_roundtrip(vals):
    r = s(vals)
    nav = (1 + r).cumprod()
    back = nav / nav.shift(1).fillna(1.0) - 1
    np.testing.assert_allclose(back.to_numpy(), r.to_numpy(), atol=1e-12)
