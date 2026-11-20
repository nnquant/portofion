# portofion

Multi-user platform that tracks many trading strategies. AI agents anywhere report daily returns
through one API. portofion stores them, computes metrics, compares strategies and builds portfolios.

```
Strategy ─┬─ Branch (main, low-turnover, …) ─┬─ Version v1  (immutable params + code_ref, own return series)
          │                                  ├─ Version v2
          │                                  └─ Version v3  ◀ head
          └─ Branch …
Portfolio = members (pinned versions, branch heads, other portfolios) + weighting scheme
```

## Layout

| path | what |
|---|---|
| `engine/` | `portofion_engine` — pure numpy/pandas metrics, calendar alignment, portfolio simulation |
| `server/` | `portofion_server` — FastAPI + SQLAlchemy/Alembic (PostgreSQL), domain services, CLI |
| `client/` | `portofion_client` — agent SDK (`Portofion.report(...)`) and `portofion-report` CLI |
| `web/` | React + Vite + TanStack Router/Query + ECharts SPA |
| `deploy/` | Dockerfile, production compose, dev compose (Postgres only) |
| `docs/metrics.md` | exact metric and portfolio definitions |
| `scripts/` | `dev.ps1`, `check.ps1`, `smoke_agent.py` |

## Develop

```powershell
./scripts/dev.ps1            # Postgres (docker, :55432) + API with reload (:8000) + Vite (:5173)
uv run portofion user create you@example.com --superuser
uv run portofion seed --workspace demo --owner you@example.com   # ~120 synthetic strategies, 6 portfolios
./scripts/check.ps1          # ruff, pytest (engine + server + SDK), tsc, vitest, vite build
```

Server tests use a `portofion_test` database on the dev Postgres. Set `PORTOFION_TEST_DATABASE_URL`
to use another database; without either, they start a throwaway testcontainers Postgres.
Run `uv run pytest -m perf` to include the performance tests.

## Deploy (LAN)

```bash
cp deploy/.env.example deploy/.env      # set passwords, PORTOFION_BASE_URL, bootstrap admin
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml --profile backup up -d   # optional nightly pg_dump
```

The app container runs migrations on start and serves the API, the web UI, `/llms.txt`, `/docs`
and the SDK wheel (`/sdk/…`) on one port.

## Agents

1. In the UI open **Settings → API keys** and create a key. The page shows a one-line installer for the
   agent's machine, and a prompt to paste into the agent itself:

   ```bash
   curl -fsSL <base>/skill/install.sh | PORTOFION_API_KEY=pfn_... sh                 # macOS / Linux
   $env:PORTOFION_API_KEY='pfn_...'; irm <base>/skill/install.ps1 | iex               # Windows PowerShell
   ```

   The installer puts the **portofion skill** (`SKILL.md`, `reference.md` and a stdlib-only
   `scripts/portofion.py`) into `~/.claude/skills/`, and into `~/.codex/skills/` when `~/.codex` exists.
   It saves the key to `~/.portofion/config.json` and checks it. `PORTOFION_SKILL_DIRS` picks other
   folders. The same bundle is at `<base>/skill/portofion.zip` for manual upload.
2. Agents without skill support read `<base>/llms.txt`. It covers the full contract: object model, units,
   dates, NAV input, corrections, limits and error codes.
3. Report with one call:

```bash
curl -s $PORTOFION_URL/api/v1/ingest -H "Authorization: Bearer $PORTOFION_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"strategy":"xs-momo","branch":"main","version":{"label":"v4","params":{"lookback":20}},
       "returns":{"points":[["2026-11-18",0.0012]]}}'
```

```python
from portofion_client import Portofion          # pip install <base>/sdk/portofion_client-0.1.0-py3-none-any.whl
Portofion().report("xs-momo", branch="main", version="v4", params={"lookback": 20}, returns=daily_returns)
```

Missing strategies, branches and versions are created automatically. A new version becomes the branch
head unless the head is locked in the UI. Re-sending a point is a no-op. A changed value is recorded
as a revision.

## Conventions

* Metrics follow `oaks_cta.metrics`: arithmetic annualization `mean × P`, rf = 0, sample std,
  Calmar = annualized return / |max drawdown|. CAGR is reported separately. `P` defaults to 252 and can be set per strategy.
* Colors: red up / green down by default (workspace setting).
* Portfolio results are cached under a fingerprint of everything they depend on: spec, weights,
  resolved branch heads, member data revisions, settings and engine version. Ingests mark dependent
  portfolios (including nested parents) stale and refresh them in the background.
