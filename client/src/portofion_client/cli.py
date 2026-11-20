"""portofion-report: push a CSV of daily returns from the shell.

    portofion-report --strategy xs-momo --branch main --version v4 returns.csv
    portofion-report --strategy xs-momo --kind nav --date-col dt --value-col nav nav.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

from portofion_client import Portofion, PortofionError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="portofion-report")
    ap.add_argument("csv", help="CSV file with a date column and a value column ('-' for stdin)")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--branch")
    ap.add_argument("--version")
    ap.add_argument("--kind", default="return", choices=["return", "log_return", "nav"])
    ap.add_argument("--date-col", default="date")
    ap.add_argument("--value-col", default=None, help="defaults to the first non-date column")
    ap.add_argument("--extra", action="append", default=[], help="extra series column(s) to send as-is")
    ap.add_argument("--params", help="JSON object of version params")
    ap.add_argument("--code-ref")
    ap.add_argument("--stage", choices=["research", "paper", "live", "retired"])
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--api-key")
    a = ap.parse_args(argv)

    fh = sys.stdin if a.csv == "-" else open(a.csv, newline="", encoding="utf-8-sig")  # noqa: SIM115
    with fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("empty CSV", file=sys.stderr)
        return 2
    cols = list(rows[0])
    vcol = a.value_col or next(c for c in cols if c != a.date_col and c not in a.extra)

    def col(name):
        return [(r[a.date_col], float(r[name])) for r in rows if r.get(name) not in (None, "")]

    try:
        with Portofion(a.url, a.api_key, agent="portofion-report") as pf:
            out = pf.report(a.strategy, branch=a.branch, version=a.version, returns=col(vcol), kind=a.kind,
                            extras={e: col(e) for e in a.extra}, params=json.loads(a.params) if a.params else None,
                            code_ref=a.code_ref, stage=a.stage, replace=a.replace, dry_run=a.dry_run)
    except PortofionError as e:
        print(str(e), file=sys.stderr)
        if e.details:
            print(json.dumps(e.details, indent=2)[:4000], file=sys.stderr)
        return 1
    print(json.dumps({k: out[k] for k in ("strategy", "branch", "version", "totals", "warnings", "metrics", "url")
                      if k in out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
