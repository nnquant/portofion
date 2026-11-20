from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from portofion_engine.analytics import compare as engine_compare
from portofion_server.auth.deps import WsCtx
from portofion_server.domain.common import periods_for, ws_settings
from portofion_server.domain.refs import resolve
from portofion_server.domain.series_store import load_returns, series_rows_for_versions
from portofion_server.errors import ApiError

MAX_REFS = 20


def load_ref_series(db: Session, ctx: WsCtx, ref: str) -> tuple[pd.Series, float, str]:
    """(returns, periods_per_year, cache tag) for any ref, including portfolios."""
    r = resolve(db, ctx.ws_id, ref)
    if r.kind == "portfolio":
        from portofion_server.domain.portfolios import compute_result

        payload = compute_result(db, ctx.ws, r.portfolio)
        s = pd.Series(payload["returns"], index=pd.DatetimeIndex(payload["dates"]), dtype="float64").dropna()
        return s, float(payload["periods_per_year"]), payload["fingerprint"]
    if r.version is None:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([])), periods_for(ctx.ws, r.strategy), "empty"
    s = load_returns(db, [r.version.id])[r.version.id]
    rev = series_rows_for_versions(db, [r.version.id]).get(r.version.id, (0, 0))[1]
    return s, periods_for(ctx.ws, r.strategy), f"{r.version.id}:{rev}"


def compare(db: Session, ctx: WsCtx, refs: list[str], *, start: date | None, end: date | None,
            corr_freq: str = "D", rolling: int = 63) -> tuple[dict, str]:
    refs = [x for x in (r.strip() for r in refs) if x]
    if not refs:
        raise ApiError(422, "VALIDATION_ERROR", "no refs given")
    if len(refs) > MAX_REFS:
        raise ApiError(422, "VALIDATION_ERROR", f"at most {MAX_REFS} refs")
    series, periods, tags = {}, {}, []
    for ref in refs:
        if ref in series:
            continue
        s, p, tag = load_ref_series(db, ctx, ref)
        series[ref], periods[ref] = s, p
        tags.append(tag)
    st = ws_settings(ctx.ws)
    out = engine_compare(series, periods=periods, rf=st["rf"], var_level=st["var_level"], start=start,
                         end=end, corr_freq=corr_freq, rolling=rolling)
    out["periods"] = periods
    from portofion_server.domain.common import stable_hash

    etag = stable_hash([tags, start, end, corr_freq, rolling, st])
    return out, etag
