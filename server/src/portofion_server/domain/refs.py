"""Ref grammar shared by URLs, portfolio members and the SDK.

    strat               default branch, follows head
    strat/branch        follows the branch head
    strat/branch@label  pinned version
    strat@label         pinned version on the default branch
    pf:slug             a portfolio
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from portofion_server.db.models import Branch, Portfolio, Strategy, Version
from portofion_server.domain.common import LABEL_RE, SLUG_RE
from portofion_server.errors import ApiError


@dataclass(frozen=True)
class Ref:
    kind: Literal["strategy", "branch", "version", "portfolio"]
    strategy: str | None = None
    branch: str | None = None
    label: str | None = None
    portfolio: str | None = None

    def __str__(self) -> str:
        if self.kind == "portfolio":
            return f"pf:{self.portfolio}"
        s = self.strategy or ""
        if self.branch:
            s += f"/{self.branch}"
        if self.label:
            s += f"@{self.label}"
        return s


def _bad(raw: str, why: str) -> ApiError:
    return ApiError(422, "REF_INVALID", f"invalid ref {raw!r}: {why}",
                    hint="use strat, strat/branch, strat/branch@label or pf:slug")


def parse_ref(raw: str) -> Ref:
    raw = (raw or "").strip()
    if raw.startswith("pf:"):
        slug = raw[3:]
        if not SLUG_RE.match(slug):
            raise _bad(raw, "bad portfolio slug")
        return Ref("portfolio", portfolio=slug)
    label = None
    if "@" in raw:
        raw_part, _, label = raw.partition("@")
        if not LABEL_RE.match(label):
            raise _bad(raw, "bad version label")
    else:
        raw_part = raw
    strat, _, branch = raw_part.partition("/")
    if not SLUG_RE.match(strat):
        raise _bad(raw, "bad strategy slug")
    if branch and not SLUG_RE.match(branch):
        raise _bad(raw, "bad branch name")
    if label:
        return Ref("version", strat, branch or None, label)
    if branch:
        return Ref("branch", strat, branch)
    return Ref("strategy", strat)


@dataclass
class Resolved:
    ref: Ref
    kind: Literal["version", "branch", "portfolio"]
    strategy: Strategy | None = None
    branch: Branch | None = None
    version: Version | None = None  # pinned version, or the current head for branch refs
    portfolio: Portfolio | None = None

    @property
    def canonical(self) -> str:
        if self.kind == "portfolio":
            return f"pf:{self.portfolio.slug}"
        if self.kind == "branch":
            return f"{self.strategy.slug}/{self.branch.name}"
        return f"{self.strategy.slug}/{self.branch.name}@{self.version.label}"


def resolve(db: Session, ws_id: int, ref: Ref | str) -> Resolved:
    if isinstance(ref, str):
        ref = parse_ref(ref)
    if ref.kind == "portfolio":
        pf = db.scalar(select(Portfolio).where(Portfolio.workspace_id == ws_id, Portfolio.slug == ref.portfolio))
        if pf is None:
            raise ApiError(404, "NOT_FOUND", f"portfolio {ref.portfolio!r} not found")
        return Resolved(ref, "portfolio", portfolio=pf)
    st = db.scalar(select(Strategy).where(Strategy.workspace_id == ws_id, Strategy.slug == ref.strategy))
    if st is None:
        raise ApiError(404, "NOT_FOUND", f"strategy {ref.strategy!r} not found")
    if ref.branch:
        br = db.scalar(select(Branch).where(Branch.strategy_id == st.id, Branch.name == ref.branch))
    else:
        br = db.get(Branch, st.default_branch_id) if st.default_branch_id else None
    if br is None:
        raise ApiError(404, "NOT_FOUND", f"branch {ref.branch or '(default)'!r} of {st.slug!r} not found")
    if ref.kind == "version":
        v = db.scalar(select(Version).where(Version.branch_id == br.id, Version.label == ref.label))
        if v is None:
            raise ApiError(404, "NOT_FOUND", f"version {ref!s} not found")
        return Resolved(ref, "version", st, br, v)
    head = db.get(Version, br.head_version_id) if br.head_version_id else None
    return Resolved(ref, "branch", st, br, head)


def resolve_version_ref(db: Session, ws_id: int, ref: Ref | str) -> Version:
    r = resolve(db, ws_id, ref)
    if r.kind == "portfolio" or r.version is None:
        raise ApiError(422, "REF_INVALID", f"{ref!s} does not point to a version")
    return r.version
