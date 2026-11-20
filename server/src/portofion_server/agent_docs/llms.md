# portofion — reporting guide for agents

portofion tracks daily strategy performance. You report **daily simple returns** (plus optional extra
series and scalars) for a strategy version. The platform computes all metrics, comparisons and portfolios.

Base URL: `{{BASE_URL}}`
OpenAPI: `{{BASE_URL}}/openapi.json` · interactive docs: `{{BASE_URL}}/docs`
Python SDK: `pip install {{SDK_WHEEL}}`

## Auth

Every call sends an API key created in the web UI (Settings → API keys):

    Authorization: Bearer pfn_XXXXXXXX_....

The key belongs to one workspace; everything you write lands there. `GET /api/v1/agent/whoami` checks it.

## Object model

    Strategy (slug)            e.g. xs-momo
      └─ Branch (name)         e.g. main, low-turnover        — a named line of development
           └─ Version (label)  e.g. v1, v2, 2026-11-a          — immutable params + code_ref, own return series

* A branch has a **head** version. A newly created version becomes the head automatically (unless the head
  is locked in the UI).
* A version's `label`, `params` and `code_ref` never change. If you change parameters or code, report under
  a **new label** (or omit `label` and send `params` — the server reuses a version with identical
  params+code_ref, otherwise creates `v{n}`).
* Several versions may run in parallel; each keeps its own series.

Refs (used for reading and portfolios): `strat`, `strat/branch`, `strat/branch@label`, `pf:portfolio-slug`.
Slugs and branch names: lowercase `[a-z0-9._-]`, ≤ 64 chars.

## The one call: `POST /api/v1/ingest`

Creates anything missing (strategy, branch, version) and upserts the points, in one atomic call.

```bash
curl -s {{BASE_URL}}/api/v1/ingest \
  -H "Authorization: Bearer $PORTOFION_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "strategy": "xs-momo",
    "branch": "main",
    "version": {"label": "v4", "params": {"lookback": 20, "top_n": 50}, "code_ref": "a1b2c3d", "stage": "paper"},
    "returns": {"points": [["2026-11-17", 0.0012], ["2026-11-18", -0.0031]]},
    "series": {"turnover": {"points": [["2026-11-18", 0.21]]}, "gross_exposure": {"points": {"2026-11-18": 1.6}}},
    "scalars": {"capacity_usd": 50000000},
    "client": {"agent": "cn-alpha-runner", "host": "box-7"}
  }'
```

Python without the SDK:

```python
import httpx
r = httpx.post("{{BASE_URL}}/api/v1/ingest", headers={"Authorization": f"Bearer {KEY}"}, json=payload, timeout=60)
r.raise_for_status(); print(r.json()["series"]["returns"])
```

With the SDK:

```python
from portofion_client import Portofion
pf = Portofion("{{BASE_URL}}", api_key=KEY)          # or env PORTOFION_URL / PORTOFION_API_KEY
pf.report("xs-momo", branch="main", version="v4", params={"lookback": 20}, code_ref=pf.git_sha(),
          returns=daily_returns_series,               # pd.Series indexed by date, dict, or [(date, r)]
          extras={"turnover": turnover_series}, scalars={"capacity_usd": 5e7})
```

### Field reference

| field | meaning |
|---|---|
| `strategy` | slug string, or `{"slug","name","kind":"strategy"/"benchmark","description","tags":[],"periods_per_year"}` |
| `branch` | name string or `{"name","forked_from":"strat/branch@label"}`; omitted → the default branch (`main` when new) |
| `version` | label string or `{"label","params","code_ref","code_repo","stage","live_since","description","make_head"}`; omitted → current head (creates `v1` if none) |
| `returns.kind` | `return` (default, simple), `log_return`, or `nav` |
| `returns.points` | `[[date, value], ...]` or `{date: value}` or `[{"date","value"}]` |
| `returns.replace` | `true` = the points sent become the full series (others are deleted) |
| `returns.delete` | list of dates to remove |
| `series.<name>` | extra daily series, same point formats; optional `agg` (`sum`/`mean`/`last`/`compound`), `unit` |
| `scalars.<name>` | a number, or `{"value", "as_of"}`; latest value wins |
| `on_existing` | `update` (default; corrections are recorded as revisions), `skip`, or `error` |
| `dry_run` | validate and report what would happen, write nothing |
| `strict` | turn warnings (outliers, percent units) into errors |

Optional header `Idempotency-Key: <unique string>` makes retries safe: the same key + same body returns the
stored response with `"replayed": true`. Pushing the same points twice is harmless anyway (`unchanged`).

## Units and dates — read carefully

* Returns are **decimals**: `0.012` means +1.2%. Sending `1.2` for 1.2% triggers a `PERCENT_UNITS` warning.
* The return dated `d` is close(d−1) → close(d) on the strategy's own trading calendar.
* Dates are `YYYY-MM-DD` strings. No timestamps, no future dates.
* **Omit days without data.** Never send `null`, `NaN` or `0` as a placeholder (0 means flat P&L).
* `nav` input: returns are computed from consecutive NAVs. The first point is used only as the anchor unless
  `base_nav` is given or an earlier NAV for this version is already stored. For a daily push of NAV, just send
  today's NAV after the first day; on the very first push send at least two days.
* Annualization uses 252 periods per year by default; set `strategy.periods_per_year` (e.g. 365 for crypto).

## Patterns

**Daily incremental push** — send only the new day(s):
`{"strategy": "...", "branch": "main", "version": "v4", "returns": {"points": [["2026-11-19", 0.0007]]}}`

**Backfill** — at most 20,000 points per series and 200,000 per request; chunk longer histories
(the SDK chunks automatically). Use `POST /api/v1/ingest/batch` with `{"items": [...]}` (≤ 100 items) to push
many versions in one request; each item succeeds or fails on its own.

**Resume** — `GET /api/v1/agent/status?strategy=xs-momo` lists every branch/version with each series'
`last_date`, so you can send only what's missing.

**Corrections** — re-send the corrected points; the old values are kept as revisions and shown in the UI.

**Read back** — `GET /api/v1/agent/series?ref=xs-momo/main@v4&names=returns,turnover` (key needs `read` scope).

**Portfolio weights** (for portfolios whose scheme is `reported`):

```json
PUT /api/v1/ingest/portfolio-weights
{"portfolio": "core", "reports": [{"date": "2026-11-18", "weights": {"xs-momo/main": 0.6, "carry/main": 0.4}}]}
```

A report dated `d` sets the weights held during `d`; weights drift with returns until the next report.
Weights summing to < 1 leave the rest in cash.

## Response

```json
{"ok": true, "batch_id": 812, "replayed": false,
 "strategy": {"slug": "xs-momo", "created": false}, "branch": {"name": "main", "created": false},
 "version": {"label": "v4", "id": 1203, "created": true, "is_head": true},
 "series": {"returns": {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": 0,
                        "first_date": "2016-01-04", "last_date": "2026-11-18", "n_points": 2620}},
 "warnings": [], "metrics": {"sharpe": 1.41, "ann_ret": 0.18, "max_dd": -0.12}, "url": "..."}
```

## Errors

All errors: `{"ok": false, "error": {"code", "message", "hint", "details": [{"loc", "date", "code", "message"}]}}`.
On any error **nothing** is written.

| HTTP | code | fix |
|---|---|---|
| 401 | `UNAUTHORIZED` | send a valid, unrevoked key |
| 403 | `FORBIDDEN` | the key lacks the `ingest`/`read` scope |
| 409 | `VERSION_CONFLICT` | the label exists with different params/code_ref → use a new label |
| 409 | `POINT_CONFLICT` | `on_existing: "error"` and values differ |
| 409 | `IDEMPOTENCY_MISMATCH` | reused an Idempotency-Key with a different body |
| 413 | `BATCH_TOO_LARGE` | split into smaller chunks |
| 422 | `VALIDATION_ERROR` | see `details`: `BAD_DATE`, `NULL_VALUE`, `NON_FINITE`, `DUPLICATE_DATE`, `DATE_OUT_OF_RANGE`, `RETURN_BELOW_MINUS_ONE`, `NAV_NOT_POSITIVE`, `BAD_SERIES_NAME` |
| 422 | `REF_INVALID` | malformed ref |

Warnings (data is written and flagged for review): `OUTLIER` (|r| above the workspace limit, default 50%),
`PERCENT_UNITS`, `GAP` (unusually long gap between dates), `HEAD_LOCKED`.
