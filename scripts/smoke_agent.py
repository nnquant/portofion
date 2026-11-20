"""End-to-end smoke test from an agent's point of view.

    PORTOFION_URL=http://127.0.0.1:8000 PORTOFION_API_KEY=pfn_... uv run python scripts/smoke_agent.py

Pushes a synthetic history through the SDK (chunked), reads it back, and checks that the server's
metrics equal what the engine computes locally.
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np
import pandas as pd

from portofion_client import Portofion
from portofion_engine.metrics import summary


def main() -> int:
    pf = Portofion(agent="smoke")
    who = pf.whoami()
    print(f"workspace={who['workspace']} key={who['key']} scopes={who['scopes']}")
    rng = np.random.default_rng(int(time.time()))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=3000)
    r = pd.Series(rng.normal(0.0003, 0.009, len(idx)), index=idx)
    label = f"smoke-{int(time.time())}"
    out = pf.report("smoke-test", branch="main", version=label, params={"seed": label}, returns=r,
                    extras={"turnover": r.abs() * 10}, scalars={"capacity_usd": 1e7}, chunk=1000)
    print(f"calls={out['calls']} totals={out['totals']['returns']} url={out['url']}")
    local = summary(r, 252)
    server = out["metrics"]
    for k in ("sharpe", "ann_ret", "ann_vol", "max_dd"):
        a, b = local[k], server[k]
        ok = math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
        print(f"  {k:8s} local={a:+.6f} server={b:+.6f} {'ok' if ok else 'MISMATCH'}")
        if not ok:
            return 1
    back = pf.series(f"smoke-test/main@{label}", ["returns"])
    assert len(back) == len(r), (len(back), len(r))
    assert pf.last_date("smoke-test", version=label) == idx[-1].strftime("%Y-%m-%d")
    # idempotent re-push
    again = pf.report("smoke-test", branch="main", version=label, returns=r.iloc[-5:])
    assert again["totals"]["returns"]["unchanged"] == 5
    print("smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
