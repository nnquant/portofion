"""Portfolio simulation on the union calendar of its members.

Semantics (docs/metrics.md, "Portfolio"):
  * w_t are begin-of-day weights held during t; R_t = Σ w_i r_i − cost_t; cash earns 0.
  * Between rebalances weights drift: w̃ = w (1 + r) / (1 + R).
  * Rebalance dates = start ∪ schedule ∪ {t : eligible set changes}.
    For ``reported`` = start ∪ effective report dates.
  * Targets at t use only rows strictly before t (no look-ahead).
  * asof = min last date over active members; members lagging more than
    ``stale_days`` behind the freshest member exit at their own last date.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from portofion_engine.calendar import align
from portofion_engine.portfolio.estimators import (
    prev_obs_count,
    prev_vol,
    shrink_to_diag,
    window_cov,
)
from portofion_engine.portfolio.riskparity import RiskParityError, erc_weights, risk_contributions
from portofion_engine.portfolio.schedule import scheduled_mask
from portofion_engine.portfolio.spec import PortfolioSpec


@dataclass
class PortfolioResult:
    returns: pd.Series
    weights: pd.DataFrame  # begin-of-day weights, members + "cash"
    contributions: pd.DataFrame
    rebalances: list[pd.Timestamp]
    turnover: pd.Series
    risk_contrib: dict[str, float] | None
    start: pd.Timestamp | None
    asof: pd.Timestamp | None
    lagging: list[str]
    excluded: list[str]
    periods_per_year: float
    warnings: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.returns.empty


def _empty(keys, warnings, periods_per_year, lagging=(), excluded=()) -> PortfolioResult:
    idx = pd.DatetimeIndex([])
    return PortfolioResult(
        returns=pd.Series(dtype="float64", index=idx),
        weights=pd.DataFrame(columns=[*keys, "cash"], index=idx, dtype="float64"),
        contributions=pd.DataFrame(columns=list(keys), index=idx, dtype="float64"),
        rebalances=[],
        turnover=pd.Series(dtype="float64", index=idx),
        risk_contrib=None,
        start=None,
        asof=None,
        lagging=list(lagging),
        excluded=list(excluded),
        periods_per_year=periods_per_year,
        warnings=warnings,
    )


def simulate(
    series: Mapping[str, pd.Series],
    spec: PortfolioSpec,
    *,
    weights: Mapping[str, float] | None = None,
    reported: pd.DataFrame | None = None,
    periods: Mapping[str, float] | None = None,
    asof: date | None = None,
) -> PortfolioResult:
    keys = list(series)
    periods = periods or {}
    warnings: list[dict] = []
    nonempty = {k: s for k, s in series.items() if s is not None and not s.empty}
    excluded = [k for k in keys if k not in nonempty]
    for k in excluded:
        warnings.append({"code": "EMPTY_MEMBER", "member": k})
    ppy = float(max([periods.get(k, 252) for k in nonempty] or [252]))
    if not nonempty:
        return _empty(keys, warnings, ppy, excluded=excluded)

    # --- as-of and lagging members -------------------------------------------------
    last = {k: s.index[-1] for k, s in nonempty.items()}
    max_last = max(last.values())
    stale_cut = max_last - pd.Timedelta(days=spec.stale_days)
    active = [k for k in nonempty if last[k] >= stale_cut]
    lagging = [k for k in nonempty if k not in active]
    for k in lagging:
        warnings.append({"code": "LAGGING_MEMBER", "member": k, "last_date": last[k].date().isoformat()})
    asof_ts = min(last[k] for k in active)
    if asof is not None:
        asof_ts = min(asof_ts, pd.Timestamp(asof))

    cols = list(nonempty)
    aligned, observed = align({k: nonempty[k] for k in cols}, end=asof_ts)
    idx = aligned.index
    A = aligned.to_numpy()
    alive = ~np.isnan(A)
    T, N = A.shape
    if T == 0:
        return _empty(keys, warnings, ppy, lagging, excluded)

    L = spec.lookback
    if spec.is_vol_scheme:
        cnt = prev_obs_count(observed, L)
        eligible = alive & (cnt >= spec.effective_min_obs)
        vol = prev_vol(aligned, L) if spec.scheme == "inverse_vol" else None
    else:
        eligible = alive.copy()
        vol = None

    # --- reported weights -> effective positions -------------------------------------
    report_at: dict[int, np.ndarray] = {}
    if spec.scheme == "reported":
        if reported is None or reported.empty:
            warnings.append({"code": "NO_REPORTED_WEIGHTS"})
            return _empty(keys, warnings, ppy, lagging, excluded)
        rep = reported.sort_index()
        rep.index = pd.DatetimeIndex(pd.to_datetime(rep.index)).normalize()
        unknown = [c for c in rep.columns if c not in cols]
        if unknown:
            warnings.append({"code": "UNKNOWN_WEIGHT_MEMBERS", "members": unknown})
        rep = rep.reindex(columns=cols).fillna(0.0)
        for d, row in rep.iterrows():
            pos = int(idx.searchsorted(d, side="left"))
            if pos >= T:
                continue
            report_at[pos] = row.to_numpy(dtype="float64")

    # --- start --------------------------------------------------------------------------
    any_e = eligible.any(axis=1)
    if not any_e.any():
        warnings.append({"code": "NO_ELIGIBLE_DATES"})
        return _empty(keys, warnings, ppy, lagging, excluded)
    earliest = int(np.argmax(any_e))
    if isinstance(spec.start, date):
        s0 = int(idx.searchsorted(pd.Timestamp(spec.start), side="left"))
        s0 = max(s0, earliest)
    elif spec.start == "earliest":
        s0 = earliest
    else:
        all_e = eligible.all(axis=1)
        if all_e.any():
            s0 = int(np.argmax(all_e))
        else:
            warnings.append({"code": "NO_COMMON_START"})
            s0 = earliest
    if spec.scheme == "reported":
        first_rep = min(report_at) if report_at else T
        if first_rep > s0:
            s0 = first_rep
        else:
            # carry the latest report at or before s0
            prior = [p for p in report_at if p <= s0]
            report_at[s0] = report_at[max(prior)]
    if s0 >= T:
        warnings.append({"code": "NO_DATES_AFTER_START"})
        return _empty(keys, warnings, ppy, lagging, excluded)

    sched = scheduled_mask(idx, spec.rebalance)
    base = np.array([float((weights or {}).get(k, 0.0)) for k in cols])
    if spec.scheme == "fixed" and weights is not None:
        missing = [k for k in weights if k not in cols]
        if missing:
            warnings.append({"code": "WEIGHT_FOR_EMPTY_MEMBER", "members": missing})
    warned: set[str] = set()

    def warn_once(code: str, **kw):
        if code not in warned:
            warned.add(code)
            warnings.append({"code": code, **kw})

    def target(t: int, e: np.ndarray) -> np.ndarray:
        tgt = np.zeros(N)
        if spec.scheme == "reported":
            tgt = report_at[t].copy()
            dead = (tgt != 0) & ~alive[t]
            if dead.any():
                warn_once("REPORTED_WEIGHT_ON_DEAD_MEMBER", date=idx[t].date().isoformat())
                tgt[dead] = 0.0
            return tgt
        if not e.any():
            return tgt
        if spec.scheme == "fixed":
            if spec.renormalize:
                s_all, s_e = base.sum(), base[e].sum()
                if abs(s_e) > 1e-12:
                    tgt[e] = base[e] * (s_all / s_e)
            else:
                tgt[e] = base[e]
        elif spec.scheme == "equal":
            tgt[e] = spec.gross / e.sum()
        elif spec.scheme == "inverse_vol":
            v = vol[t]
            ok = e & np.isfinite(v) & (v > 1e-8)
            if (e & ~ok).any():
                warn_once("ZERO_VOL_EXCLUDED")
            if ok.any():
                inv = 1.0 / v[ok]
                tgt[ok] = spec.gross * inv / inv.sum()
        elif spec.scheme == "risk_parity":
            sel = np.flatnonzero(e)
            cov = window_cov(A, t, L, sel)
            if cov is None:
                tgt[e] = spec.gross / e.sum()
                return tgt
            cov = shrink_to_diag(cov, spec.shrinkage)
            try:
                w = erc_weights(cov)
            except RiskParityError:
                warn_once("RISK_PARITY_FALLBACK", date=idx[t].date().isoformat())
                inv = 1.0 / np.sqrt(np.diag(cov))
                w = inv / inv.sum()
            tgt[sel] = spec.gross * w
        return tgt

    W = np.zeros((T, N))
    CASH = np.zeros(T)
    R = np.full(T, np.nan)
    rebal_pos: list[int] = []
    turnover: dict[int, float] = {}
    w = np.zeros(N)
    cash = 1.0
    prev_e: np.ndarray | None = None
    t_end = T
    for t in range(s0, T):
        e = eligible[t]
        if spec.scheme == "reported":
            rebal = t == s0 or t in report_at
            if not rebal:
                dead = (w != 0) & ~alive[t]
                if dead.any():
                    cash += w[dead].sum()
                    w[dead] = 0.0
        else:
            rebal = t == s0 or bool(sched[t]) or (prev_e is not None and bool((e != prev_e).any()))
        cost = 0.0
        if rebal:
            tgt = target(t, e)
            if t != s0:
                to = float(np.abs(tgt - w).sum())
                turnover[t] = to
                cost = to * spec.cost_bps / 1e4
            else:
                turnover[t] = 0.0
            w = tgt
            cash = 1.0 - tgt.sum()
            rebal_pos.append(t)
        r_t = np.where(alive[t], A[t], 0.0)
        r_t = np.nan_to_num(r_t, nan=0.0)
        Rt = float((w * r_t).sum()) - cost
        W[t] = w
        CASH[t] = cash
        R[t] = Rt
        tot = 1.0 + Rt
        if tot <= 1e-12:
            warnings.append({"code": "PORTFOLIO_WIPED_OUT", "date": idx[t].date().isoformat()})
            R[t] = -1.0
            t_end = t + 1
            break
        w = w * (1.0 + r_t) / tot
        cash = (cash - cost) / tot
        prev_e = e

    sl = slice(s0, t_end)
    out_idx = idx[sl]
    rets = pd.Series(R[sl], index=out_idx, name="portfolio")
    wdf = pd.DataFrame(W[sl], index=out_idx, columns=cols)
    wdf["cash"] = CASH[sl]
    for k in excluded:
        wdf[k] = 0.0
    wdf = wdf[[*keys, "cash"]]
    r_mat = np.nan_to_num(np.where(alive[sl], A[sl], 0.0), nan=0.0)
    contrib = pd.DataFrame(W[sl] * r_mat, index=out_idx, columns=cols)
    for k in excluded:
        contrib[k] = 0.0
    contrib = contrib[keys]
    to_series = pd.Series({idx[p]: v for p, v in turnover.items() if p < t_end}, dtype="float64")

    rc = None
    if rebal_pos:
        p = rebal_pos[-1]
        wl = W[p]
        sel = np.flatnonzero(np.abs(wl) > 1e-12)
        cov = window_cov(A, p, L, sel) if len(sel) else None
        if cov is not None and np.all(np.isfinite(cov)):
            rcv = risk_contributions(wl[sel], cov)
            if np.all(np.isfinite(rcv)):
                rc = {cols[i]: float(v) for i, v in zip(sel, rcv, strict=True)}

    return PortfolioResult(
        returns=rets,
        weights=wdf,
        contributions=contrib,
        rebalances=[idx[p] for p in rebal_pos if p < t_end],
        turnover=to_series,
        risk_contrib=rc,
        start=out_idx[0] if len(out_idx) else None,
        asof=out_idx[-1] if len(out_idx) else None,
        lagging=lagging,
        excluded=excluded,
        periods_per_year=ppy,
        warnings=warnings,
    )
