"""portofion agent SDK.

    from portofion_client import Portofion
    pf = Portofion()   # PORTOFION_URL / PORTOFION_API_KEY, else ~/.portofion/config.json (written by the skill installer)
    pf.report("xs-momo", branch="main", version="v4", params={...}, returns=daily_returns)
"""

from __future__ import annotations

import gzip
import json
import math
import os
import random
import subprocess
import time
import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

__all__ = ["Portofion", "PortofionError", "git_sha"]
__version__ = "0.1.0"

DEFAULT_CHUNK = 10_000
GZIP_OVER = 64 * 1024


class PortofionError(Exception):
    def __init__(self, status: int, code: str, message: str, hint: str | None = None, details: Any = None):
        super().__init__(f"[{status} {code}] {message}" + (f" (hint: {hint})" if hint else ""))
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details


def git_sha(path: str = ".", short: bool = True) -> str | None:
    """Current commit of the repo at ``path`` (None when not a git checkout)."""
    try:
        args = ["git", "-C", path, "rev-parse", *(["--short", "HEAD"] if short else ["HEAD"])]
        out = subprocess.run(args, capture_output=True, text=True, timeout=5)
        sha = out.stdout.strip()
        return sha or None
    except Exception:
        return None


def _date_str(d: Any) -> str:
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    if hasattr(d, "strftime"):  # pandas Timestamp / numpy datetime via pandas
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def to_points(data: Any) -> list[list]:
    """pd.Series / dict / iterable of pairs -> sorted [[YYYY-MM-DD, float], ...]; NaN/None dropped."""
    if data is None:
        return []
    if (hasattr(data, "items") and not isinstance(data, Mapping)) or isinstance(data, Mapping):  # pandas Series
        pairs = list(data.items())
    else:
        pairs = [tuple(p) for p in data]
    out = {}
    for d, v in pairs:
        if v is None:
            continue
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            continue
        out[_date_str(d)] = v
    return [[d, out[d]] for d in sorted(out)]


def load_config() -> dict[str, str | None]:
    """Credentials: environment first, then the config file the skill installer writes."""
    path = Path(os.environ.get("PORTOFION_CONFIG") or Path.home() / ".portofion" / "config.json")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    return {"url": os.environ.get("PORTOFION_URL") or cfg.get("url"),
            "api_key": os.environ.get("PORTOFION_API_KEY") or cfg.get("api_key")}


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class Portofion:
    def __init__(self, url: str | None = None, api_key: str | None = None, *, timeout: float = 60.0,
                 retries: int = 5, agent: str | None = None):
        cfg = load_config() if not (url and api_key) else {}
        self.url = (url or cfg.get("url") or "").rstrip("/")
        self.api_key = api_key or cfg.get("api_key")
        if not self.url or not self.api_key:
            raise ValueError("set url/api_key, PORTOFION_URL/PORTOFION_API_KEY, or ~/.portofion/config.json")
        self.retries = retries
        self.client_info = {"agent": agent or os.environ.get("PORTOFION_AGENT") or "sdk",
                            "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
                            "sdk": __version__}
        self._http = httpx.Client(base_url=self.url, timeout=timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}",
                                           "User-Agent": f"portofion-client/{__version__}"})

    # --- transport ----------------------------------------------------------------------

    def _request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
                 idempotency_key: str | None = None) -> Any:
        headers = {}
        content = None
        if json_body is not None:
            raw = json.dumps(json_body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
            if len(raw) > GZIP_OVER:
                raw = gzip.compress(raw)
                headers["Content-Encoding"] = "gzip"
            content = raw
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        delay = 0.5
        for attempt in range(self.retries + 1):
            try:
                r = self._http.request(method, path, content=content, params=params, headers=headers)
            except httpx.TransportError:
                if attempt >= self.retries:
                    raise
            else:
                if r.status_code < 500 and r.status_code != 429:
                    return self._parse(r)
                if attempt >= self.retries:
                    return self._parse(r)
            time.sleep(delay + random.random() * delay)
            delay = min(delay * 2, 30)
        raise RuntimeError("unreachable")

    @staticmethod
    def _parse(r: httpx.Response) -> Any:
        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
            raise PortofionError(r.status_code, err.get("code", "HTTP_ERROR"), err.get("message", r.text[:300]),
                                 err.get("hint"), err.get("details"))
        return data if data is not None else r.text

    # --- reporting ----------------------------------------------------------------------

    def report(
        self,
        strategy: str | Mapping[str, Any],
        *,
        branch: str | None = None,
        version: str | None = None,
        returns: Any = None,
        kind: str = "return",
        base_nav: float | None = None,
        extras: Mapping[str, Any] | None = None,
        scalars: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        code_ref: str | None = None,
        code_repo: str | None = None,
        stage: str | None = None,
        live_since: Any = None,
        description: str | None = None,
        make_head: bool | None = None,
        replace: bool = False,
        on_existing: str = "update",
        strict: bool = False,
        dry_run: bool = False,
        chunk: int = DEFAULT_CHUNK,
    ) -> dict:
        """Report returns (and extras/scalars) for one version. Long histories are chunked.

        Returns the last server response, with ``totals`` summed across chunks.
        """
        ver: dict[str, Any] = {}
        if version is not None:
            ver["label"] = version
        for k, v in (("params", params), ("code_ref", code_ref), ("code_repo", code_repo), ("stage", stage),
                     ("description", description), ("make_head", make_head)):
            if v is not None:
                ver[k] = dict(v) if k == "params" else v
        if live_since is not None:
            ver["live_since"] = _date_str(live_since)
        ident: dict[str, Any] = {"strategy": strategy if isinstance(strategy, str) else dict(strategy),
                                 "on_existing": on_existing, "strict": strict, "dry_run": dry_run,
                                 "client": self.client_info}
        if branch is not None:
            ident["branch"] = branch
        if ver:
            ident["version"] = ver

        payloads: list[dict] = []
        rpts = to_points(returns)
        if replace and len(rpts) > chunk:
            raise ValueError("replace=True needs the full series in one call; raise chunk or split manually")
        for i, part in enumerate(_chunks(rpts, chunk) if rpts else []):
            ret: dict[str, Any] = {"kind": kind, "points": part, "replace": replace}
            if kind == "nav" and i == 0 and base_nav is not None:
                ret["base_nav"] = base_nav
            payloads.append({**ident, "returns": ret})
        for name, data in (extras or {}).items():
            agg = None
            if isinstance(data, Mapping) and "points" in data:
                agg = data.get("agg")
                data = data["points"]
            for part in _chunks(to_points(data), chunk):
                s: dict[str, Any] = {"points": part}
                if agg:
                    s["agg"] = agg
                payloads.append({**ident, "series": {name: s}})
        if not payloads:
            payloads.append(dict(ident))
        if scalars:
            payloads[0]["scalars"] = {k: (v if isinstance(v, Mapping) else float(v)) for k, v in scalars.items()}
        # later NAV chunks anchor on the NAV the previous chunk stored server-side

        totals: dict[str, dict[str, int]] = {}
        last = None
        for p in payloads:
            last = self._request("POST", "/api/v1/ingest", json_body=p, idempotency_key=str(uuid.uuid4()))
            for name, st in last.get("series", {}).items():
                t = totals.setdefault(name, {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0})
                for f in t:
                    t[f] += int(st.get(f, 0) or 0)
        assert last is not None
        last["totals"] = totals
        last["calls"] = len(payloads)
        return last

    def report_batch(self, items: list[dict]) -> dict:
        """Raw batch call: each item is an /ingest body (≤ 100 items)."""
        out = {"ok": True, "n_ok": 0, "n_failed": 0, "results": []}
        for part in _chunks(items, 100):
            r = self._request("POST", "/api/v1/ingest/batch", json_body={"items": part})
            out["ok"] = out["ok"] and r["ok"]
            out["n_ok"] += r["n_ok"]
            out["n_failed"] += r["n_failed"]
            out["results"] += r["results"]
        return out

    def report_weights(self, portfolio: str, weights: Any, *, auto_add_members: bool = False,
                       replace: bool = False) -> dict:
        """weights: DataFrame (index=date, columns=refs) or {date: {ref: w}}."""
        if hasattr(weights, "iterrows"):
            reports = [{"date": _date_str(d), "weights": {str(k): float(v) for k, v in row.items() if v == v}}
                       for d, row in weights.iterrows()]
        else:
            reports = [{"date": _date_str(d), "weights": {k: float(v) for k, v in w.items()}}
                       for d, w in weights.items()]
        return self._request("PUT", "/api/v1/ingest/portfolio-weights",
                             json_body={"portfolio": portfolio, "reports": reports,
                                        "auto_add_members": auto_add_members, "replace": replace})

    # --- reading ------------------------------------------------------------------------

    def whoami(self) -> dict:
        return self._request("GET", "/api/v1/agent/whoami")

    def status(self, strategy: str | None = None) -> dict:
        return self._request("GET", "/api/v1/agent/status", params={"strategy": strategy} if strategy else None)

    def last_date(self, strategy: str, branch: str = "main", version: str | None = None,
                  series: str = "returns") -> str | None:
        """Last stored date of a series (None if nothing yet) — handy for incremental pushes."""
        try:
            st = self.status(strategy)
        except PortofionError as e:
            if e.status == 404:
                return None
            raise
        for s in st["strategies"]:
            for b in s["branches"]:
                if b["name"] != branch:
                    continue
                label = version or b["head"]
                for v in b["versions"]:
                    if v["label"] == label:
                        return v["series"].get(series, {}).get("last_date")
        return None

    def series(self, ref: str, names: list[str] | None = None, *, as_frame: bool = True):
        data = self._request("GET", "/api/v1/agent/series",
                             params={"ref": ref, **({"names": ",".join(names)} if names else {})})
        if not as_frame:
            return data
        try:
            import pandas as pd
        except ImportError:
            return data
        return pd.DataFrame(data["series"], index=pd.DatetimeIndex(data["dates"]))

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
