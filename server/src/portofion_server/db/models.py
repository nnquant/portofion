from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict] = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --- tenancy & auth -------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = _pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, default="")
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _now()
    disabled_at: Mapped[datetime | None]


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint("kind in ('personal','team')", name="kind"),)
    id: Mapped[int] = _pk()
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="team")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    settings_rev: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = _now()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "user_id"),
        CheckConstraint("role in ('admin','member','viewer')", name="role"),
    )
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (CheckConstraint("role in ('admin','member','viewer')", name="role"),)
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime] = _now()
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    prefix: Mapped[str] = mapped_column(Text, unique=True)
    secret_hash: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


# --- catalog ----------------------------------------------------------------------------


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug"),
        CheckConstraint("kind in ('strategy','benchmark')", name="kind"),
    )
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="strategy", server_default=text("'strategy'"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    default_branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id", ondelete="SET NULL", use_alter=True, name="fk_strategy_default_branch")
    )
    periods_per_year: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    archived_at: Mapped[datetime | None]


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("strategy_id", "name"),)
    id: Mapped[int] = _pk()
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    head_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL", use_alter=True, name="fk_branch_head"), index=True
    )
    head_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    forked_from_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL", use_alter=True, name="fk_branch_forked_from")
    )
    created_at: Mapped[datetime] = _now()
    archived_at: Mapped[datetime | None]


class BranchHeadHistory(Base):
    __tablename__ = "branch_head_history"
    id: Mapped[int] = _pk()
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = _now()
    actor: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("branch_id", "label"),
        UniqueConstraint("branch_id", "seq"),
        CheckConstraint("stage in ('research','paper','live','retired')", name="stage"),
        Index("ix_versions_params", "params", postgresql_using="gin",
              postgresql_ops={"params": "jsonb_path_ops"}),
    )
    id: Mapped[int] = _pk()
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text, index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    code_ref: Mapped[str | None] = mapped_column(Text)
    code_repo: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, default="research", server_default=text("'research'"))
    live_since: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    archived_at: Mapped[datetime | None]


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)


class StrategyTag(Base):
    __tablename__ = "strategy_tags"
    __table_args__ = (PrimaryKeyConstraint("strategy_id", "tag_id"),)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)


# --- series -----------------------------------------------------------------------------


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (
        UniqueConstraint("version_id", "name"),
        CheckConstraint("agg in ('compound','last','mean','sum')", name="agg"),
    )
    id: Mapped[int] = _pk()
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    agg: Mapped[str] = mapped_column(Text, default="last")
    unit: Mapped[str | None] = mapped_column(Text)
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    n_points: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    data_rev: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = _now()


class SeriesPoint(Base):
    """Row per point; hash-partitioned by series_id (partitions created in migration)."""

    __tablename__ = "series_points"
    __table_args__ = (
        PrimaryKeyConstraint("series_id", "d"),
        {"postgresql_partition_by": "HASH (series_id)"},
    )
    series_id: Mapped[int] = mapped_column(BigInteger)
    d: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    first_seen: Mapped[date] = mapped_column(Date)


class SeriesRevision(Base):
    __tablename__ = "series_revisions"
    __table_args__ = (Index("ix_series_revisions_sd", "series_id", "d"),)
    id: Mapped[int] = _pk()
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"))
    d: Mapped[date] = mapped_column(Date)
    old_value: Mapped[float | None] = mapped_column(Float)
    new_value: Mapped[float | None] = mapped_column(Float)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_batches.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()


class PointFlag(Base):
    __tablename__ = "point_flags"
    __table_args__ = (PrimaryKeyConstraint("series_id", "d", "code"),)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"))
    d: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_batches.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    acked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acked_at: Mapped[datetime | None]


class Scalar(Base):
    __tablename__ = "scalars"
    __table_args__ = (PrimaryKeyConstraint("version_id", "name"),)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)
    as_of: Mapped[date | None] = mapped_column(Date)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_batches.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = _now()


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    __table_args__ = (
        Index("ux_ingest_idem", "workspace_id", "idempotency_key", unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
    )
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id", ondelete="SET NULL"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    request_sha256: Mapped[str | None] = mapped_column(Text)
    client: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    received_at: Mapped[datetime] = _now()
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# --- portfolios -------------------------------------------------------------------------


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_rev: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    weights_rev: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    archived_at: Mapped[datetime | None]


class PortfolioMember(Base):
    __tablename__ = "portfolio_members"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "key"),
        CheckConstraint("num_nonnulls(version_id, branch_id, child_portfolio_id) = 1", name="one_ref"),
        CheckConstraint("ref_kind in ('version','branch','portfolio')", name="ref_kind"),
    )
    id: Mapped[int] = _pk()
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(Text)
    ref_kind: Mapped[str] = mapped_column(Text)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id", ondelete="RESTRICT"), index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"), index=True)
    child_portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("portfolios.id", ondelete="RESTRICT"), index=True
    )
    weight: Mapped[float | None] = mapped_column(Float)


class PortfolioWeight(Base):
    __tablename__ = "portfolio_weights"
    __table_args__ = (PrimaryKeyConstraint("portfolio_id", "d", "member_id"),)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    d: Mapped[date] = mapped_column(Date)
    member_id: Mapped[int] = mapped_column(ForeignKey("portfolio_members.id", ondelete="CASCADE"))
    weight: Mapped[float] = mapped_column(Float)


# --- caches -----------------------------------------------------------------------------

METRIC_COLUMNS = (
    "total_ret", "ann_ret", "cagr", "ann_vol", "sharpe", "sortino", "max_dd", "max_dd_days",
    "longest_dd_days", "cur_dd", "cur_dd_days", "calmar", "win_rate", "profit_factor", "skew",
    "kurt", "var", "cvar", "best", "worst",
)


class VersionMetrics(Base):
    __tablename__ = "version_metrics"
    __table_args__ = (PrimaryKeyConstraint("version_id", "window"),)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"))
    window: Mapped[str] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(Text)
    settings_hash: Mapped[str] = mapped_column(Text)
    data_rev: Mapped[int] = mapped_column(BigInteger)
    computed_at: Mapped[datetime] = _now()
    n: Mapped[int] = mapped_column(Integer)
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    total_ret: Mapped[float | None] = mapped_column(Float)
    ann_ret: Mapped[float | None] = mapped_column(Float)
    cagr: Mapped[float | None] = mapped_column(Float)
    ann_vol: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    sortino: Mapped[float | None] = mapped_column(Float)
    max_dd: Mapped[float | None] = mapped_column(Float)
    max_dd_days: Mapped[int | None] = mapped_column(Integer)
    longest_dd_days: Mapped[int | None] = mapped_column(Integer)
    cur_dd: Mapped[float | None] = mapped_column(Float)
    cur_dd_days: Mapped[int | None] = mapped_column(Integer)
    calmar: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    skew: Mapped[float | None] = mapped_column(Float)
    kurt: Mapped[float | None] = mapped_column(Float)
    var: Mapped[float | None] = mapped_column(Float)
    cvar: Mapped[float | None] = mapped_column(Float)
    best: Mapped[float | None] = mapped_column(Float)
    worst: Mapped[float | None] = mapped_column(Float)


class VersionSummary(Base):
    __tablename__ = "version_summary"
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id", ondelete="CASCADE"), primary_key=True)
    engine_version: Mapped[str] = mapped_column(Text)
    settings_hash: Mapped[str] = mapped_column(Text)
    data_rev: Mapped[int] = mapped_column(BigInteger)
    spark: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    ret_1d: Mapped[float | None] = mapped_column(Float)
    ret_mtd: Mapped[float | None] = mapped_column(Float)
    ret_ytd: Mapped[float | None] = mapped_column(Float)
    ret_1y: Mapped[float | None] = mapped_column(Float)
    cur_dd: Mapped[float | None] = mapped_column(Float)
    cur_dd_days: Mapped[int | None] = mapped_column(Integer)
    n_revisions: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    n_open_flags: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_report_at: Mapped[datetime | None]
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = _now()


class PortfolioResultRow(Base):
    __tablename__ = "portfolio_results"
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = _now()
    asof: Mapped[date | None] = mapped_column(Date)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spark: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    payload: Mapped[bytes] = mapped_column(LargeBinary)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ws_at", "workspace_id", "at"),)
    id: Mapped[int] = _pk()
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text)
    target_kind: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = _now()


N_POINT_PARTITIONS = 16
