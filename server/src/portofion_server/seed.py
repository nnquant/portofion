"""Synthetic demo data: a factor model drives realistic cross-correlations."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from portofion_server.auth.deps import Principal, WsCtx
from portofion_server.db import session_factory
from portofion_server.db.models import Membership, User, Workspace
from portofion_server.domain.ingest import ingest_item
from portofion_server.domain.portfolios import (
    compute_result,
    get_portfolio,
    report_weights,
    save_portfolio,
)
from portofion_server.schemas import (
    IngestIn,
    MemberIn,
    PortfolioIn,
    PortfolioSpecIn,
    WeightReportIn,
)

MARKETS = {
    "cn": {"ppy": 244, "factors": [0, 1, 2], "bench": "csi300"},
    "us": {"ppy": 252, "factors": [0, 3, 2], "bench": "spx"},
    "crypto": {"ppy": 365, "factors": [4], "bench": "btc"},
    "cta": {"ppy": 252, "factors": [0, 3, 4], "bench": None},
}
STYLES = ["momo", "rev", "value", "carry", "statarb", "trend", "vol", "basis", "event", "quality", "pairs", "ml"]
BRANCHES = ["main", "low-turnover", "lev2x", "cost-adj", "fast", "hedged"]


def _calendar(market: str, start: date, end: date) -> pd.DatetimeIndex:
    if market == "crypto":
        return pd.date_range(start, end, freq="D")
    days = pd.bdate_range(start, end)
    hol = set()
    for y in range(start.year, end.year + 1):
        if market == "cn":
            cny = pd.Timestamp(year=y, month=1, day=21) + pd.Timedelta(days=(y * 11) % 25)
            hol |= set(pd.date_range(cny, cny + pd.Timedelta(days=6)))
            hol |= set(pd.date_range(f"{y}-10-01", f"{y}-10-07"))
            hol |= {pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-05-01")}
        else:
            hol |= {pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-07-04"), pd.Timestamp(f"{y}-12-25")}
            nov = pd.date_range(f"{y}-11-01", f"{y}-11-30")
            hol.add([d for d in nov if d.weekday() == 3][3])
    return days[~days.isin(list(hol))]


def _factors(rng, idx: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(idx)
    vols = np.array([0.011, 0.007, 0.005, 0.006, 0.03])
    corr = np.array([
        [1, .3, .1, .5, .2], [.3, 1, .0, .1, .0], [.1, .0, 1, .1, .0], [.5, .1, .1, 1, .1], [.2, .0, .0, .1, 1],
    ])
    cov = corr * np.outer(vols, vols)
    x = rng.multivariate_normal(np.zeros(5), cov, size=n)
    # volatility regimes
    regime = 1 + 0.8 * (np.sin(np.arange(n) / 400.0) > 0.7)
    return pd.DataFrame(x * regime[:, None], index=idx)


def _ctx(db, ws: Workspace, owner: User | None) -> WsCtx:
    return WsCtx(ws, "admin", Principal(user=owner))


def run_seed(ws_slug: str, n_strategies: int, owner_email: str | None, seed: int, with_portfolios: bool) -> None:
    rng = np.random.default_rng(seed)
    db = session_factory()()
    t0 = time.time()
    try:
        owner = db.scalar(select(User).where(User.email == owner_email.lower())) if owner_email else None
        ws = db.scalar(select(Workspace).where(Workspace.slug == ws_slug))
        if ws is None:
            ws = Workspace(slug=ws_slug, name=ws_slug.title(), kind="team", settings={})
            db.add(ws)
            db.flush()
        if owner is not None and db.get(Membership, (ws.id, owner.id)) is None:
            db.add(Membership(workspace_id=ws.id, user_id=owner.id, role="admin"))
        db.commit()
        ctx = _ctx(db, ws, owner)
        end = (datetime.now(UTC) - timedelta(days=1)).date()
        start = date(2010, 1, 4)
        all_days = pd.date_range(start, end, freq="D")
        F = _factors(rng, all_days)

        def push(body: dict):
            ingest_item(db, ctx, IngestIn.model_validate(body))
            db.commit()

        # benchmarks
        for mk, info in MARKETS.items():
            if not info["bench"]:
                continue
            cal = _calendar(mk, start, end)
            r = F.loc[cal, info["factors"][0]] * 1.1 + 0.0002
            push({"strategy": {"slug": info["bench"], "name": info["bench"].upper(), "kind": "benchmark",
                               "periods_per_year": info["ppy"], "tags": [mk, "benchmark"]},
                  "returns": {"points": _pts(r)}})

        heads: dict[str, list[str]] = {mk: [] for mk in MARKETS}
        n_versions = 0
        for i in range(n_strategies):
            mk = list(MARKETS)[i % len(MARKETS)]
            info = MARKETS[mk]
            style = STYLES[(i // len(MARKETS)) % len(STYLES)]
            slug = f"{mk}-{style}-{i // (len(MARKETS) * len(STYLES)) + 1:02d}"
            s_start = date(2010 + int(rng.integers(0, 10)), 1, 4) + timedelta(days=int(rng.integers(0, 300)))
            cal = _calendar(mk, s_start, end)
            load = rng.normal(0, 0.25, len(info["factors"]))
            base_alpha = rng.normal(0.00012, 0.00015)
            idio_vol = rng.uniform(0.004, 0.014)
            n_br = int(rng.choice([1, 1, 2, 2, 3, 4]))
            tags = [mk, style] + (["prod"] if rng.random() < 0.3 else [])
            for b in range(n_br):
                bname = BRANCHES[b]
                lev = 2.0 if bname == "lev2x" else 1.0
                n_ver = int(rng.integers(1, 7))
                for vi in range(n_ver):
                    v_start = cal[min(int(len(cal) * vi / (n_ver + 1.5)), len(cal) - 30)]
                    vcal = cal[cal >= v_start]
                    alpha = base_alpha * (1 + 0.08 * vi) * (0.8 if bname == "cost-adj" else 1.0)
                    fx = F.loc[vcal, info["factors"]].to_numpy() @ load
                    noise = rng.normal(0, idio_vol, len(vcal))
                    r = pd.Series(lev * (alpha + fx + noise), index=vcal)
                    if rng.random() < 0.05:
                        r.iloc[int(rng.integers(0, len(r)))] = 0.62  # a bad print -> OUTLIER flag
                    stage = "live" if vi == n_ver - 1 and rng.random() < 0.5 else ("paper" if vi == n_ver - 1 else "research")
                    live_since = vcal[-min(120, len(vcal) - 1)].date().isoformat() if stage == "live" else None
                    turnover = pd.Series(np.abs(rng.normal(0.3 if bname != "low-turnover" else 0.1, 0.05, len(vcal))),
                                         index=vcal)
                    body = {
                        "strategy": {"slug": slug, "name": f"{mk.upper()} {style} #{i + 1}", "tags": tags,
                                     "periods_per_year": info["ppy"]},
                        "branch": bname,
                        "version": {"label": f"v{vi + 1}", "stage": stage, "live_since": live_since,
                                    "code_ref": f"{rng.integers(0, 16**7):07x}",
                                    "params": {"lookback": int(rng.choice([5, 10, 20, 60])), "top_n": int(rng.choice([20, 50, 100])),
                                               "lev": lev, "rev": vi + 1}},
                        "returns": {"points": _pts(r)},
                        "series": {"turnover": {"points": _pts(turnover)},
                                   "gross_exposure": {"points": _pts(pd.Series(lev * rng.uniform(0.9, 1.1, len(vcal)), index=vcal))}},
                        "scalars": {"capacity_usd": float(rng.choice([5e6, 2e7, 5e7, 2e8])),
                                    "avg_hold_days": float(np.round(rng.uniform(1, 20), 1))},
                        "client": {"agent": f"seed-{mk}", "host": f"box-{i % 7}"},
                    }
                    push(body)
                    n_versions += 1
                    if rng.random() < 0.15 and len(r) > 10:  # a correction -> revisions
                        fix = r.iloc[-5:] + rng.normal(0, 0.0005, 5)
                        push({"strategy": slug, "branch": bname, "version": f"v{vi + 1}",
                              "returns": {"points": _pts(fix)}})
                if b == 0:
                    heads[mk].append(f"{slug}/{bname}")
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{n_strategies} strategies, {n_versions} versions, {time.time() - t0:.0f}s")

        if with_portfolios:
            _seed_portfolios(db, ctx, heads, rng)
        print(f"seeded {n_strategies} strategies / {n_versions} versions into '{ws_slug}' in {time.time() - t0:.0f}s")
    finally:
        db.close()


def _pts(s: pd.Series) -> list:
    return [[d.strftime("%Y-%m-%d"), float(v)] for d, v in s.items()]


def _seed_portfolios(db, ctx: WsCtx, heads: dict[str, list[str]], rng) -> None:
    if ctx.principal.user is None:
        ctx.principal.user = User(id=None, email="seed@local", name="seed")  # satisfies require_user
    def save(slug, name, spec, refs, weights=None):
        members = [MemberIn(ref=r, weight=(weights[i] if weights else None)) for i, r in enumerate(refs)]
        try:
            get_portfolio(db, ctx, slug)
            pf = save_portfolio(db, ctx, PortfolioIn(slug=slug, name=name, spec=spec, members=members), existing_slug=slug)
        except Exception:
            db.rollback()
            pf = save_portfolio(db, ctx, PortfolioIn(slug=slug, name=name, spec=spec, members=members))
        return pf

    core = heads["cn"][:3] + heads["us"][:3] + heads["cta"][:2]
    save("core", "Core", PortfolioSpecIn(scheme="fixed", rebalance="monthly"), core,
         [0.2, 0.1, 0.1, 0.2, 0.1, 0.1, 0.1, 0.1][: len(core)])
    save("rp-cn", "CN risk parity", PortfolioSpecIn(scheme="risk_parity", rebalance="monthly", lookback=126),
         heads["cn"][:8])
    save("ivol-global", "Global inverse vol", PortfolioSpecIn(scheme="inverse_vol", rebalance="weekly", lookback=63),
         heads["us"][:4] + heads["crypto"][:3] + heads["cta"][:3])
    save("ew-crypto", "Crypto equal", PortfolioSpecIn(scheme="equal", rebalance="none", start="earliest"),
         heads["crypto"][:5])
    save("multi", "Multi-sleeve", PortfolioSpecIn(scheme="fixed", rebalance="quarterly"),
         ["pf:core", "pf:rp-cn", "pf:ivol-global"], [0.5, 0.3, 0.2])
    alloc_refs = heads["us"][:3]
    save("agent-alloc", "Agent allocation", PortfolioSpecIn(scheme="reported"), alloc_refs)
    days = pd.date_range(end=datetime.now(UTC).date() - timedelta(days=1), periods=36, freq="BMS")
    reports = []
    for d in days:
        w = rng.dirichlet(np.ones(len(alloc_refs))) * 0.95
        reports.append(WeightReportIn(date=d.date().isoformat(), weights=dict(zip(alloc_refs, map(float, w), strict=True))))
    report_weights(db, ctx, "agent-alloc", reports, auto_add=False, replace=True)
    for slug in ("core", "rp-cn", "ivol-global", "ew-crypto", "multi", "agent-alloc"):
        compute_result(db, ctx.ws, get_portfolio(db, ctx, slug))
    print("  portfolios: core, rp-cn, ivol-global, ew-crypto, multi (nested), agent-alloc (reported)")
