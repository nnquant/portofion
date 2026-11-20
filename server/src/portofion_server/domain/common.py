from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from portofion_engine import ENGINE_VERSION
from portofion_server.auth.deps import Principal
from portofion_server.db.models import AuditLog, Strategy, Workspace
from portofion_server.errors import ApiError
from portofion_server.settings import DEFAULT_WORKSPACE_SETTINGS

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SERIES_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
# Tag colors, in the order new tags take them (neighbours sit far apart on the hue wheel).
TAG_COLORS = ("blue", "orange", "green", "violet", "amber", "cyan", "pink", "lime", "red", "indigo", "teal", "yellow")


def check_slug(value: str, what: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise ApiError(
            422, "VALIDATION_ERROR", f"invalid {what} {value!r}",
            hint="lowercase letters, digits, '.', '_' or '-', starting with a letter or digit, max 64 chars",
        )
    return value


def check_label(value: str) -> str:
    if not isinstance(value, str) or not LABEL_RE.match(value):
        raise ApiError(422, "VALIDATION_ERROR", f"invalid version label {value!r}",
                       hint="letters, digits, '.', '_', '+' or '-', max 64 chars; no '/' or '@'")
    return value


def ws_settings(ws: Workspace) -> dict[str, Any]:
    return {**DEFAULT_WORKSPACE_SETTINGS, **(ws.settings or {})}


def periods_for(ws: Workspace, strategy: Strategy | None) -> float:
    if strategy is not None and strategy.periods_per_year:
        return float(strategy.periods_per_year)
    return float(ws_settings(ws)["periods_per_year"])


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:24]


def metrics_settings_hash(ws: Workspace, strategy: Strategy | None, live_since) -> str:
    s = ws_settings(ws)
    return stable_hash({
        "engine": ENGINE_VERSION,
        "ppy": periods_for(ws, strategy),
        "rf": s["rf"],
        "var": s["var_level"],
        "live": live_since,
    })


def audit(
    db: Session,
    ws_id: int | None,
    principal: Principal | None,
    action: str,
    target_kind: str | None = None,
    target_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(AuditLog(
        workspace_id=ws_id,
        actor_user_id=principal.user_id if principal else None,
        actor_key_id=principal.key_id if principal else None,
        action=action,
        target_kind=target_kind,
        target_id=target_id,
        before=before,
        after=after,
    ))
