from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["research", "paper", "live", "retired"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- ingestion -------------------------------------------------------------------------------


class StrategyIn(_In):
    slug: str
    name: str | None = None
    kind: Literal["strategy", "benchmark"] | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    periods_per_year: float | None = Field(default=None, gt=0, le=366)


class BranchIn(_In):
    name: str
    forked_from: str | None = None


class VersionIn(_In):
    label: str | None = None
    params: dict[str, Any] | None = None
    code_ref: str | None = None
    code_repo: str | None = None
    stage: Stage | None = None
    live_since: date | None = None
    description: str | None = None
    meta: dict[str, Any] | None = None
    make_head: bool | None = None


class ReturnsIn(_In):
    kind: Literal["return", "log_return", "nav"] = "return"
    base_nav: float | None = Field(default=None, gt=0)
    points: Any = None
    replace: bool = False
    delete: list[str] = Field(default_factory=list)


class SeriesIn(_In):
    points: Any = None
    agg: Literal["compound", "last", "mean", "sum"] | None = None
    unit: str | None = None
    replace: bool = False
    delete: list[str] = Field(default_factory=list)


class ScalarIn(_In):
    value: float
    as_of: date | None = None


class IngestIn(_In):
    strategy: str | StrategyIn
    branch: str | BranchIn | None = None
    version: str | VersionIn | None = None
    returns: ReturnsIn | None = None
    series: dict[str, SeriesIn] = Field(default_factory=dict)
    scalars: dict[str, float | ScalarIn] = Field(default_factory=dict)
    on_existing: Literal["update", "skip", "error"] = "update"
    dry_run: bool = False
    strict: bool = False
    client: dict[str, Any] | None = None


class IngestBatchIn(_In):
    items: list[IngestIn] = Field(max_length=100)


class WeightReportIn(_In):
    date: str
    weights: dict[str, float]


class PortfolioWeightsIn(_In):
    portfolio: str
    auto_add_members: bool = False
    reports: list[WeightReportIn]
    replace: bool = False


# --- portfolios -----------------------------------------------------------------------------


class PortfolioSpecIn(_In):
    spec_version: int = 1
    scheme: Literal["fixed", "equal", "inverse_vol", "risk_parity", "reported"] = "fixed"
    rebalance: Literal["daily", "weekly", "monthly", "quarterly", "none"] = "monthly"
    lookback: int = Field(default=63, ge=5, le=2520)
    min_obs: int | None = Field(default=None, ge=2)
    start: Literal["common", "earliest"] | date = "common"
    cost_bps: float = Field(default=0.0, ge=0, le=1000)
    gross: float = Field(default=1.0, gt=0, le=10)
    renormalize: bool = True
    stale_days: int | None = Field(default=None, ge=0, le=3650)
    shrinkage: float = Field(default=0.1, ge=0, le=1)


class MemberIn(_In):
    ref: str
    weight: float | None = None


class PortfolioIn(_In):
    slug: str
    name: str | None = None
    description: str = ""
    spec: PortfolioSpecIn = Field(default_factory=PortfolioSpecIn)
    members: list[MemberIn] = Field(default_factory=list, max_length=500)


class PortfolioPreviewIn(_In):
    spec: PortfolioSpecIn = Field(default_factory=PortfolioSpecIn)
    members: list[MemberIn] = Field(max_length=500)
    slug: str | None = None  # when previewing edits of an existing portfolio (reported weights)


# --- catalog edits ------------------------------------------------------------------------


class StrategyPatch(_In):
    name: str | None = None
    description: str | None = None
    kind: Literal["strategy", "benchmark"] | None = None
    tags: list[str] | None = None
    periods_per_year: float | None = Field(default=None, gt=0, le=366)
    clear_periods_per_year: bool = False
    default_branch: str | None = None
    archived: bool | None = None


class BranchPatch(_In):
    head: str | None = None  # version label
    head_locked: bool | None = None
    archived: bool | None = None


class VersionPatch(_In):
    stage: Stage | None = None
    live_since: date | None = None
    clear_live_since: bool = False
    description: str | None = None
    archived: bool | None = None


# --- auth / workspace ---------------------------------------------------------------------


class LoginIn(_In):
    email: str
    password: str


class SignupIn(_In):
    email: str
    password: str = Field(min_length=8)
    name: str = ""


class InviteAcceptIn(_In):
    email: str | None = None
    password: str | None = Field(default=None, min_length=8)
    name: str = ""


class WorkspaceIn(_In):
    slug: str
    name: str


class WorkspaceSettingsPatch(_In):
    name: str | None = None
    periods_per_year: float | None = Field(default=None, gt=0, le=366)
    rf: float | None = Field(default=None, ge=-0.5, le=1)
    var_level: float | None = Field(default=None, gt=0.5, lt=1)
    outlier_abs: float | None = Field(default=None, gt=0)
    stale_days: int | None = Field(default=None, ge=0, le=3650)
    up_color: Literal["red", "green"] | None = None


class MemberRolePatch(_In):
    role: Literal["admin", "member", "viewer"]


class InviteIn(_In):
    email: str | None = None
    role: Literal["admin", "member", "viewer"] = "member"
    days: int = Field(default=7, ge=1, le=90)


class ApiKeyIn(_In):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["ingest", "read"]] = Field(default_factory=lambda: ["ingest", "read"])
    expires_days: int | None = Field(default=None, ge=1, le=3650)


class UserCreateIn(_In):
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    is_superuser: bool = False


class FlagAckIn(_In):
    version_id: int
    dates: list[str] | None = None
    codes: list[str] | None = None
