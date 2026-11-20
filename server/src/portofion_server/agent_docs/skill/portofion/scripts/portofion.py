#!/usr/bin/env python3
"""portofion agent CLI - Python 3.8+, standard library only.

    python portofion.py whoami
    python portofion.py configure --key pfn_...
    python portofion.py status [--strategy SLUG]
    python portofion.py report --strategy SLUG [--branch B] [--version V] [--params JSON] --csv FILE
    python portofion.py series REF [--names returns,turnover] [--out FILE]
    python portofion.py weights --portfolio SLUG --csv FILE

Credentials: PORTOFION_URL / PORTOFION_API_KEY, else ~/.portofion/config.json (PORTOFION_CONFIG overrides
the path). Output is JSON on stdout; errors go to stderr with exit code 1 (API) or 2 (input).
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import gzip
import io
import json
import math
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_URL = "{{BASE_URL}}"
SKILL_VERSION = "1"
CHUNK = 10_000
GZIP_OVER = 64 * 1024
DATE_COLS = ("date", "d", "dt", "day", "trade_date", "trading_day", "datetime", "time", "timestamp")
MISSING = {"", "nan", "na", "n/a", "#n/a", "null", "none", "-"}


class InputError(Exception):
    """Bad local input (exit code 2)."""


class ApiError(Exception):
    def __init__(self, status: int, body):
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.status = status
        self.code = err.get("code", "HTTP_ERROR")
        self.message = err.get("message") or (str(body)[:300] if body else "")
        self.hint = err.get("hint")
        self.details = err.get("details")
        super().__init__(f"[{status} {self.code}] {self.message}")


# --- config & transport --------------------------------------------------------------------


def config_path() -> Path:
    return Path(os.environ.get("PORTOFION_CONFIG") or Path.home() / ".portofion" / "config.json")


def load_config() -> dict:
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    url = os.environ.get("PORTOFION_URL") or cfg.get("url") or DEFAULT_URL
    return {"url": url.rstrip("/"), "api_key": os.environ.get("PORTOFION_API_KEY") or cfg.get("api_key")}


def http(method: str, url: str, headers: dict, body: bytes | None):
    """One HTTP exchange -> (status, raw body). Tests replace this function."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Client:
    def __init__(self, url: str, key: str | None, retries: int = 4):
        if not key:
            raise InputError("no API key: run `portofion.py configure --key pfn_...` (keys are created in the "
                             f"web UI at {url} under Settings -> API keys) or set PORTOFION_API_KEY")
        self.url, self.key, self.retries = url.rstrip("/"), key, retries

    def request(self, method: str, path: str, body=None, params: dict | None = None, idem: str | None = None):
        url = self.url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json",
                   "User-Agent": f"portofion-skill/{SKILL_VERSION}"}
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
            if len(data) > GZIP_OVER:
                data = gzip.compress(data)
                headers["Content-Encoding"] = "gzip"
        if idem:
            headers["Idempotency-Key"] = idem
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                status, raw = http(method, url, headers, data)
            except OSError as e:  # URLError, timeouts and connection resets on every Python version
                if attempt >= self.retries:
                    msg = f"cannot reach {self.url}: {e}"
                    raise ApiError(0, {"error": {"code": "UNREACHABLE", "message": msg}}) from e
            else:
                if (status < 500 and status != 429) or attempt >= self.retries:
                    return self._parse(status, raw)
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 2, 30)
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(status: int, raw: bytes):
        text = raw.decode("utf-8", "replace") if raw else ""
        try:
            body = json.loads(text) if text else None
        except ValueError:
            body = text
        if status >= 400:
            raise ApiError(status, body)
        return body


# --- reading local data --------------------------------------------------------------------


def norm_date(s, where: str) -> str:
    raw = str(s).strip()
    t = re.split(r"[ T]", raw)[0]
    for fmt, part in (("%Y-%m-%d", t), ("%Y/%m/%d", t), ("%Y.%m.%d", t), ("%Y%m%d", t[:8] if t.isdigit() else "")):
        try:
            return datetime.strptime(part, fmt).date().isoformat()
        except ValueError:
            continue
    raise InputError(f"{where}: unrecognised date {raw!r} (use YYYY-MM-DD)")


def is_date(s) -> bool:
    try:
        norm_date(s, "")
        return True
    except InputError:
        return False


def norm_value(v, where: str, scale: float = 1.0):
    if v is None:
        return None
    pct = False
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip()
        if s.lower() in MISSING:
            return None
        if "," in s:
            if "." not in s:  # 0,0123 could be a decimal comma or 1,234 a thousands separator
                raise InputError(f"{where}: ambiguous number {v!r}; use '.' as the decimal separator")
            s = s.replace(",", "")
        pct = s.endswith("%")
        try:
            x = float(s[:-1] if pct else s)
        except ValueError:
            raise InputError(f"{where}: not a number: {v!r}") from None
    if math.isnan(x):
        return None
    if math.isinf(x):
        raise InputError(f"{where}: infinite value")
    return x / 100.0 if pct else x * scale


def finish(pairs: list, label: str) -> list:
    """Sort, reject duplicate dates, drop missing values."""
    seen: dict = {}
    for d, _v, where in pairs:
        if d in seen:
            raise InputError(f"{label}: date {d} appears twice ({seen[d]} and {where})")
        seen[d] = where
    return [[d, v] for d, v, _ in sorted(pairs, key=lambda p: p[0]) if v is not None]


def read_csv(path: str):
    """-> (header or None, rows). Header is detected when the first cell is not a date."""
    if path == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except OSError as e:
            raise InputError(f"cannot read {path}: {e}") from None
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if r and any(c.strip() for c in r)]
    if not rows:
        raise InputError(f"{path}: no rows")
    if is_date(rows[0][0]):
        return None, rows
    return [c.strip() for c in rows[0]], rows[1:]


def col_index(header, rows, name: str | None, default: int | None, path: str, what: str) -> int:
    if name is None:
        if default is None:
            raise InputError(f"{path}: cannot tell which column is the {what}; pass --{what}-col")
        return default
    if name.isdigit():
        if int(name) >= max(len(r) for r in rows):
            raise InputError(f"{path}: has no column {name}")
        return int(name)
    if header is None:
        raise InputError(f"{path}: has no header row; give --{what}-col as a 0-based column number")
    low = [h.lower() for h in header]
    if name.lower() not in low:
        raise InputError(f"{path}: no column {name!r}; columns: {', '.join(header)}")
    return low.index(name.lower())


def csv_columns(path: str, date_col: str | None, value_cols: list, scale_first: float = 1.0) -> dict:
    """Read one date column and several value columns. value_cols: [name or None]; None = auto."""
    header, rows = read_csv(path)
    di = None
    if date_col is None and header:
        di = next((i for i, h in enumerate(header) if h.lower() in DATE_COLS), None)
    di = col_index(header, rows, date_col, 0 if di is None else di, path, "date")
    out = {}
    for k, name in enumerate(value_cols):
        if name is None:
            vi = next((i for i in range(len(rows[0])) if i != di), None)
            vi = col_index(header, rows, None, vi, path, "value")
        else:
            vi = col_index(header, rows, name, None, path, "value")
        pairs = []
        for n, r in enumerate(rows, start=2 if header else 1):
            if vi >= len(r) or di >= len(r):
                continue
            where = f"{path}:{n}"
            pairs.append((norm_date(r[di], where), norm_value(r[vi], where, scale_first if k == 0 else 1.0), where))
        key = header[vi] if header else str(vi)
        out[key if name is not None else "__value__"] = finish(pairs, path)
    return out


def json_points(data, label: str, scale: float = 1.0) -> list:
    if isinstance(data, dict):
        items = list(data.items())
    elif isinstance(data, list):
        items = []
        for p in data:
            if isinstance(p, dict):
                items.append((p.get("date", p.get("d")), p.get("value", p.get("v"))))
            elif isinstance(p, (list, tuple)) and len(p) == 2:
                items.append((p[0], p[1]))
            else:
                raise InputError(f"{label}: points must be [date, value] pairs, {{date: value}} or [{{date, value}}]")
    else:
        raise InputError(f"{label}: unsupported JSON shape")
    pairs = [(norm_date(d, f"{label}[{i}]"), norm_value(v, f"{label}[{i}]", scale), f"#{i}")
             for i, (d, v) in enumerate(items)]
    return finish(pairs, label)


def load_points(path: str, scale: float = 1.0, date_col: str | None = None, value_col: str | None = None) -> list:
    if path.lower().endswith(".json"):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise InputError(f"cannot read {path}: {e}") from None
        return json_points(data, path, scale)
    cols = csv_columns(path, date_col, [value_col], scale)
    return next(iter(cols.values()))


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


# --- commands ------------------------------------------------------------------------------


def cmd_whoami(cli: Client, a) -> dict:
    who = cli.request("GET", "/api/v1/agent/whoami")
    return {"url": cli.url, **who, "config": str(config_path())}


def cmd_configure(a) -> dict:
    cfg = load_config()
    url = (a.url or cfg["url"]).rstrip("/")
    who = None
    if not a.no_verify:
        who = Client(url, a.key, retries=1).request("GET", "/api/v1/agent/whoami")
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"url": url, "api_key": a.key}) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)
    return {"saved": str(p), "url": url, **(who or {})}


def cmd_status(cli: Client, a) -> list:
    st = cli.request("GET", "/api/v1/agent/status", params={"strategy": a.strategy} if a.strategy else None)
    rows = []
    for s in st["strategies"]:
        for b in s["branches"]:
            for v in b["versions"]:
                r = v["series"].get("returns", {})
                rows.append({"ref": f"{s['slug']}/{b['name']}@{v['label']}", "head": v["label"] == b["head"],
                             "stage": v["stage"], "code_ref": v["code_ref"], "first_date": r.get("first_date"),
                             "last_date": r.get("last_date"), "n": r.get("n_points", 0),
                             "series": sorted(k for k in v["series"] if k != "returns")})
    return rows


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def cmd_report(cli: Client, a) -> dict:
    scale = 0.01 if a.percent else 1.0
    if a.kind != "return" and a.percent:
        raise InputError("--percent only applies to --kind return")
    sources = [x for x in (a.csv, a.json, a.points) if x]
    if len(sources) > 1:
        raise InputError("give one of --csv, --json, --points")
    extras: dict = {}
    if a.csv:
        cols = [a.value_col] + [c.strip() for c in (a.columns or "").split(",") if c.strip()]
        got = csv_columns(a.csv, a.date_col, cols, scale)
        keys = list(got)
        returns = got[keys[0]]
        for k in keys[1:]:
            extras[k.lower()] = got[k]
    elif a.json:
        returns = load_points(a.json, scale)
    elif a.points:
        try:
            returns = json_points(json.loads(a.points), "--points", scale)
        except ValueError as e:
            raise InputError(f"--points is not JSON: {e}") from None
    else:
        returns = []
    for spec in a.series or []:
        name, _, path = spec.partition("=")
        if not path:
            raise InputError(f"--series expects NAME=FILE, got {spec!r}")
        extras[name.strip()] = load_points(path.strip())
    scalars = {}
    for spec in a.scalar or []:
        name, _, val = spec.partition("=")
        x = norm_value(val, f"--scalar {name}")
        if x is None:
            raise InputError(f"--scalar expects NAME=NUMBER, got {spec!r}")
        scalars[name.strip()] = x
    if not returns and not extras and not scalars:
        raise InputError("nothing to send: give --csv / --json / --points, --series or --scalar")

    strategy: dict = {"slug": a.strategy}
    for k, v in (("name", a.name), ("description", a.description), ("periods_per_year", a.periods_per_year)):
        if v is not None:
            strategy[k] = v
    if a.tags is not None:
        strategy["tags"] = [t.strip() for t in a.tags.split(",") if t.strip()]
    if a.benchmark:
        strategy["kind"] = "benchmark"
    version: dict = {}
    if a.version:
        version["label"] = a.version
    if a.params:
        try:
            version["params"] = json.loads(a.params)
        except ValueError as e:
            raise InputError(f"--params is not JSON: {e}") from None
        if not isinstance(version["params"], dict):
            raise InputError("--params must be a JSON object")
    code_ref = git_sha() if a.code_ref == "git" else a.code_ref
    if a.code_ref == "git" and not code_ref:
        raise InputError("--code-ref git: not inside a git checkout")
    for k, v in (("code_ref", code_ref), ("stage", a.stage), ("live_since", a.live_since)):
        if v:
            version[k] = norm_date(v, "--live-since") if k == "live_since" else v
    ident: dict = {"strategy": strategy if len(strategy) > 1 else a.strategy, "dry_run": a.dry_run,
                   "strict": a.strict, "on_existing": a.on_existing,
                   "client": {"agent": os.environ.get("PORTOFION_AGENT") or "skill",
                              "host": socket.gethostname(), "sdk": f"skill/{SKILL_VERSION}"}}
    if a.branch:
        ident["branch"] = a.branch
    if version:
        ident["version"] = version

    if a.replace and len(returns) > a.chunk:
        raise InputError(f"--replace needs the whole series in one call ({len(returns)} points > --chunk {a.chunk})")
    payloads = []
    for part in _chunks(returns, a.chunk):
        payloads.append({**ident, "returns": {"kind": a.kind, "points": part, "replace": a.replace}})
    for name, pts in extras.items():
        for part in _chunks(pts, a.chunk):
            payloads.append({**ident, "series": {name: {"points": part}}})
    if not payloads:
        payloads.append(dict(ident))
    if scalars:
        payloads[0]["scalars"] = scalars

    totals: dict = {}
    warnings: list = []
    created = {"strategy": False, "branch": False, "version": False}
    last = None
    for p in payloads:
        last = cli.request("POST", "/api/v1/ingest", body=p, idem=str(uuid.uuid4()))
        warnings += last.get("warnings") or []
        for k in created:
            created[k] = created[k] or bool((last.get(k) or {}).get("created"))
        for name, s in (last.get("series") or {}).items():
            t = totals.setdefault(name, {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0})
            for f in ("inserted", "updated", "unchanged", "deleted"):
                t[f] += int(s.get(f) or 0)
            t["first_date"], t["last_date"], t["n_points"] = s.get("first_date"), s.get("last_date"), s.get("n_points")
    assert last is not None
    v = last.get("version") or {}
    return {
        "ref": f"{(last.get('strategy') or {}).get('slug')}/{(last.get('branch') or {}).get('name')}@{v.get('label')}",
        "dry_run": a.dry_run, "calls": len(payloads),
        "created": created,
        "is_head": v.get("is_head"), "series": totals, "warnings": warnings,
        "metrics": last.get("metrics"), "url": last.get("url"),
    }


def cmd_series(cli: Client, a) -> dict | None:
    params = {"ref": a.ref}
    if a.names:
        params["names"] = a.names
    data = cli.request("GET", "/api/v1/agent/series", params=params)
    names = list(data["series"])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", *names])
    for i, d in enumerate(data["dates"]):
        w.writerow([d, *("" if data["series"][n][i] is None else repr(data["series"][n][i]) for n in names)])
    if a.out:
        Path(a.out).write_text(buf.getvalue(), encoding="utf-8")
        return {"saved": a.out, "rows": len(data["dates"]), "columns": names}
    sys.stdout.write(buf.getvalue())
    return None


def cmd_weights(cli: Client, a) -> dict:
    reports: dict = {}
    if a.json:
        try:
            data = json.loads(Path(a.json).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise InputError(f"cannot read {a.json}: {e}") from None
        items = data.items() if isinstance(data, dict) else ((r["date"], r["weights"]) for r in data)
        for d, w in items:
            reports[norm_date(d, a.json)] = {k: norm_value(x, f"{a.json} {d} {k}") for k, x in w.items()}
    else:
        header, rows = read_csv(a.csv)
        if header is None:
            raise InputError(f"{a.csv}: needs a header row (date,<ref>,... or date,ref,weight)")
        low = [h.lower() for h in header]
        long_ref = next((i for i, h in enumerate(low) if h in ("ref", "member")), None)
        if long_ref is not None and "weight" in low:
            wi = low.index("weight")
            for n, r in enumerate(rows, start=2):
                reports.setdefault(norm_date(r[0], f"{a.csv}:{n}"), {})[r[long_ref].strip()] = \
                    norm_value(r[wi], f"{a.csv}:{n}")
        else:
            for n, r in enumerate(rows, start=2):
                reports[norm_date(r[0], f"{a.csv}:{n}")] = {
                    header[i]: norm_value(r[i], f"{a.csv}:{n}") for i in range(1, min(len(r), len(header)))}
    body = {"portfolio": a.portfolio, "replace": a.replace, "auto_add_members": a.auto_add_members,
            "reports": [{"date": d, "weights": {k: v for k, v in w.items() if v is not None}}
                        for d, w in sorted(reports.items())]}
    return cli.request("PUT", "/api/v1/ingest/portfolio-weights", body=body)


# --- entry point ---------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="portofion.py", description="Report strategy performance to portofion.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami", help="check the configured key")
    c = sub.add_parser("configure", help="save url + API key to the config file")
    c.add_argument("--key", required=True)
    c.add_argument("--url")
    c.add_argument("--no-verify", action="store_true")
    s = sub.add_parser("status", help="list strategies/branches/versions and where each series ends")
    s.add_argument("--strategy")
    r = sub.add_parser("report", help="send daily returns (+ extra series / scalars) for one version")
    r.add_argument("--strategy", required=True)
    r.add_argument("--branch")
    r.add_argument("--version", help="version label, e.g. v4")
    r.add_argument("--params", help="JSON object of the version's parameters")
    r.add_argument("--code-ref", help="commit id, or 'git' for the current checkout")
    r.add_argument("--stage", choices=["research", "paper", "live", "retired"])
    r.add_argument("--live-since")
    r.add_argument("--csv", help="CSV file ('-' = stdin)")
    r.add_argument("--json", help="JSON file: [[date, value], ...] or {date: value}")
    r.add_argument("--points", help="inline JSON points")
    r.add_argument("--date-col")
    r.add_argument("--value-col")
    r.add_argument("--columns", help="more CSV columns to send as extra series, comma separated")
    r.add_argument("--kind", default="return", choices=["return", "log_return", "nav"])
    r.add_argument("--percent", action="store_true", help="values are percents (1.2 = 1.2%%)")
    r.add_argument("--series", action="append", metavar="NAME=FILE")
    r.add_argument("--scalar", action="append", metavar="NAME=VALUE")
    r.add_argument("--name")
    r.add_argument("--description")
    r.add_argument("--tags")
    r.add_argument("--benchmark", action="store_true")
    r.add_argument("--periods-per-year", type=int)
    r.add_argument("--replace", action="store_true", help="the points sent become the whole series")
    r.add_argument("--on-existing", default="update", choices=["update", "skip", "error"])
    r.add_argument("--strict", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--chunk", type=int, default=CHUNK)
    g = sub.add_parser("series", help="download stored series as CSV")
    g.add_argument("ref", help="strategy, strategy/branch or strategy/branch@version")
    g.add_argument("--names")
    g.add_argument("--out")
    w = sub.add_parser("weights", help="report portfolio weights")
    w.add_argument("--portfolio", required=True)
    src = w.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--json")
    w.add_argument("--replace", action="store_true")
    w.add_argument("--auto-add-members", action="store_true")
    return ap


def main(argv: list | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        if a.cmd == "configure":
            out = cmd_configure(a)
        else:
            cfg = load_config()
            cli = Client(cfg["url"], cfg["api_key"])
            out = {"whoami": cmd_whoami, "status": cmd_status, "report": cmd_report,
                   "series": cmd_series, "weights": cmd_weights}[a.cmd](cli, a)
    except InputError as e:
        print(json.dumps({"ok": False, "error": {"code": "INPUT_ERROR", "message": str(e)}}), file=sys.stderr)
        return 2
    except ApiError as e:
        err = {"code": e.code, "message": e.message, "hint": e.hint, "details": (e.details or [])[:50]
               if isinstance(e.details, list) else e.details}
        print(json.dumps({"ok": False, "status": e.status, "error": err}, indent=2), file=sys.stderr)
        return 1
    if out is not None:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
