# Metric & portfolio definitions

Implemented in `engine/src/portofion_engine`; tests in `engine/tests` reference these definitions.
Conventions follow `oaks_cta.metrics` (arithmetic annualization, rf = 0 by default, sample std).

## Inputs

A series is daily **simple** returns indexed by date, containing only observed days. `P` is the
annualization factor (`periods_per_year`): strategy override → workspace setting → 252. `rf` is the
annual risk-free rate (workspace setting, default 0), `rf_d = rf / P`. `n` = number of observations.
Undefined values are `null` (never a 0.0 sentinel) and sort last.

## Calendar alignment

* `O_i` = observed dates of series *i*, life `L_i = [min O_i, max O_i]`, `U` = union of all `O_i`.
* Aligned value at `t ∈ U`: the observation if `t ∈ O_i`; **0** if `t ∈ L_i` but unobserved (holiday —
  NAV carried); **null** outside the life.
* Single-series metrics use the series' **own observations only** (never zero-filled) and its own `P`.
* Pairwise statistics (correlation, beta, TE, IR) use the intersection `O_i ∩ O_j` of raw observations,
  optionally after compounding each series to weekly (W-FRI) or monthly buckets over its own observations.
  Minimum overlap: 20 observations (matrix: 60 daily / 26 weekly / 12 monthly), else `null`.
* Windows (`all`, `live`, `ytd`, `1y`, `3y`) are trailing and anchored at the series' own last date;
  `live = [live_since, last]`.

## Scalar metrics

| key | definition |
|---|---|
| `total_ret` | `Π(1+r) − 1` |
| `ann_ret` | `mean(r) · P` (arithmetic) |
| `cagr` | `NAV_end^(P/n) − 1` |
| `ann_vol` | `std(r, ddof=1) · √P` |
| `sharpe` | `(mean(r) − rf_d) / std(r) · √P` |
| `sortino` | `(mean(r) − rf_d) / √mean(min(r − rf_d, 0)²) · √P` (downside deviation over all observations) |
| `max_dd` | `min(NAV / cummax(NAV) − 1)` with `NAV_0 = 1` prepended (a first-day loss counts) |
| `max_dd_days` | calendar days from peak to recovery (or to the last date if open) of the deepest episode |
| `longest_dd_days` | longest episode in calendar days |
| `cur_dd`, `cur_dd_days` | drawdown on the last date and the age of the open episode |
| `calmar` | `ann_ret / |max_dd|` |
| `win_rate` | `count(r > 0) / count(r ≠ 0)` |
| `profit_factor` | `Σ r⁺ / |Σ r⁻|` |
| `skew`, `kurt` | pandas sample skew, excess kurtosis |
| `var`, `cvar` | historical: `VaR = quantile(r, 1 − level)` (linear), `CVaR = mean(r ≤ VaR)`; level 0.95 default |
| `best`, `worst` | max / min daily return |

Benchmark-relative (intersection): `beta = cov/var_b`, `alpha = (mean_s − β·mean_b)·P`,
`TE = std(r_s − r_b)·√P`, `IR = mean(r_s − r_b)·P / TE`.

Tables: monthly / yearly returns compound the series' own observations. Rolling Sharpe/vol use a
window of N observations (`min_periods = N`). Sparkline: NAV (with origin 1) downsampled to 64 points by LTTB.

Deliberate difference from `oaks_cta.metrics.max_drawdown`: the NAV origin of 1 is included, so a loss
on the very first day is part of the drawdown.

## Portfolio simulation (`portfolio/simulate.py`)

* Calendar `U_P` = union of member observation dates within `[start, asof]`, fill rule as above.
  A member is **alive** where its aligned return is not null.
* **Eligible** set `E_t`: alive members; for `inverse_vol` / `risk_parity` additionally ≥ `min_obs`
  real observations (default `max(20, lookback/2)`) in the previous `lookback` dates.
* Begin-of-day weights `w_t`; `R_t = Σ w_i,t · r_i,t − cost_t`; cash `1 − Σw` earns 0.
  Between rebalances weights drift: `w̃_i,t+1 = w_i,t (1 + r_i,t) / (1 + R_t)`.
  With no rebalancing this gives exactly `NAV_P = Σ w_0,i · NAV_i`.
* **Rebalance dates** = start ∪ schedule (first date of each ISO week / month / quarter present in `U_P`,
  every date for daily, none for buy-and-hold) ∪ any date where `E_t` changes (entry or exit → full
  rebalance over the new eligible set).
* **No look-ahead**: targets at `t` use only rows strictly before `t`.
* Targets:
  * `fixed` — user weights; with `renormalize` (default) alive weights are scaled by
    `Σ_all w / Σ_alive w`. Weights may be negative or not sum to 1.
  * `equal` — `gross / |E_t|`.
  * `inverse_vol` — `1/σ_i` normalized to `gross`, σ from the previous `lookback` aligned rows.
  * `risk_parity` — equal risk contribution on the sample covariance shrunk towards its diagonal
    (`δ = 0.1`), solved with damped Newton on Spinu's convex form; falls back to inverse vol with a warning.
  * `reported` — weights reported by agents; a report dated `d` applies to `d` (or the next date of `U_P`),
    drifts until the next report, unreported members are 0, exits go to cash.
* Costs: `turnover = Σ|τ − w̃|` at each rebalance after the first; `cost = turnover · bps / 1e4`.
* `start`: `common` (first date all members are eligible), `earliest`, or a date.
* `asof` = min last date over **active** members (last date within `stale_days` of the freshest member);
  lagging members exit at their own last date.
* Portfolio `P` = max member `P`.
* Nested portfolios: refs resolve into a DAG (cycles rejected, depth ≤ 8), evaluated post-order with
  memoization; a child portfolio's return series is a leaf for its parent. Branch members use the
  current head version's full history.
