---
name: portofion
description: Report trading-strategy performance to the portofion platform at {{BASE_URL}} - daily returns or NAV per strategy / branch / version, extra daily series (turnover, exposure, ...), scalar stats and portfolio weights - and check what portofion already holds. Use when a backtest, paper or live run produces daily returns that should be tracked, or when asked to upload, sync, publish or report strategy performance to portofion.
---

# portofion

portofion (`{{BASE_URL}}`) stores daily returns per strategy version and computes every metric itself
(Sharpe, drawdowns, comparisons, portfolios). Your job is to send correct daily returns under the right
identity. Full API contract: [reference.md](reference.md).

Commands use `scripts/portofion.py` in this skill's directory (Python 3.8+, standard library only; use
`python3` where `python` is missing). Every command prints JSON.

## 1. Check access

```bash
python scripts/portofion.py whoami
```

If it reports a missing key, ask the user for an API key (created in the web UI under Settings → API keys)
and save it — the command verifies the key before writing `~/.portofion/config.json`:

```bash
python scripts/portofion.py configure --key pfn_XXXXXXXX_...
```

## 2. Choose the identity

```
strategy (slug)   →  branch (name)          →  version (label)
xs-momo              main, low-turnover, …      v1, v2, …   (one exact set of params + code)
```

- List what exists before inventing names: `python scripts/portofion.py status` (or `--strategy xs-momo`).
- Slugs and branch names: lowercase `a-z 0-9 . _ -`, at most 64 characters.
- A version's params and code_ref are frozen. Changed parameters or code → **new version label**. Reusing
  a label with different params fails with `VERSION_CONFLICT`.
- A new version becomes its branch's head automatically.

## 3. Prepare the data

A CSV with a date column and a value column (header optional, extra columns allowed):

```
date,ret
2026-11-17,0.0012
2026-11-18,-0.0031
```

- **Daily simple returns as decimals**: `0.0123` = +1.23%. Numbers in percent → add `--percent`.
- The return dated `d` is close(d−1) → close(d). One row per trading day.
- **Omit days without data.** Never fill gaps with 0 (0 means flat P&L) or NaN; blank/NaN cells are skipped.
- NAV instead of returns → `--kind nav` (the first NAV only anchors). Log returns → `--kind log_return`.
- Dates: `YYYY-MM-DD` (`YYYYMMDD`, `YYYY/MM/DD` and timestamps are normalised).

## 4. Report

```bash
python scripts/portofion.py report --strategy xs-momo --branch main --version v4 \
    --params '{"lookback": 20, "top_n": 50}' --code-ref git --csv returns.csv
```

| option | use |
|---|---|
| `--value-col ret` / `--date-col dt` | pick columns (default: a column named date/dt/…, then the first other column) |
| `--columns turnover,gross_exposure` | send more columns of the same CSV as extra daily series |
| `--series turnover=turnover.csv` | extra daily series from another file |
| `--scalar capacity_usd=5e7` | latest-value statistics |
| `--code-ref git` | current commit of the working directory |
| `--stage research\|paper\|live` | version stage |
| `--name`, `--tags cn,momentum`, `--benchmark`, `--periods-per-year 365` | strategy metadata (365 for crypto) |
| `--dry-run` | validate and show what would change, write nothing |

Long histories are chunked automatically. Re-running is safe: identical points are no-ops, changed values
are kept as revisions. Send the full history on the first run; afterwards sending the full file again or
only the newest rows are both fine.

Report back to the user the `url`, the counts and the `metrics` from the output.

## 5. Act on warnings and errors

Warnings mean the data **was written** but is flagged:

| warning | action |
|---|---|
| `PERCENT_UNITS` | values look like percents → re-send with `--percent` (corrects them) |
| `OUTLIER` | verify those dates in the source; re-send fixed values if wrong |
| `GAP` | dates missing inside the series → fill from the source if they exist |
| `HEAD_LOCKED` | head is pinned in the UI; nothing to do |

Errors mean **nothing was written** (exit code 1, details on stderr):

| error | action |
|---|---|
| `VERSION_CONFLICT` | label exists with other params/code → use a new label |
| `VALIDATION_ERROR` | `details` lists the bad rows (bad date, NaN, return ≤ −100%, …) → fix and re-send |
| `UNAUTHORIZED` / `FORBIDDEN` | key invalid or lacks the `ingest` scope → ask the user |

## More

- Read back: `python scripts/portofion.py series xs-momo/main@v4 --names returns,turnover --out back.csv`
- Portfolio weights (portfolios with the `reported` scheme), CSV `date,<ref>,<ref>…` or `date,ref,weight`:
  `python scripts/portofion.py weights --portfolio core --csv weights.csv`
- From Python code: `pip install {{SDK_WHEEL}}`, then
  `from portofion_client import Portofion; Portofion().report("xs-momo", branch="main", version="v4", params={...}, returns=series)`
  (reads the same credentials).
