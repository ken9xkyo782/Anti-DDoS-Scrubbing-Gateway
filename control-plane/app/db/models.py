import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import CIDR, CITEXT, JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    admin = "admin"
    tenant_user = "tenant_user"


class UserStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class TenantStatus(StrEnum):
    active = "active"
    suspended = "suspended"


class CIDRStatus(StrEnum):
    active = "active"
    revoked = "revoked"


class ApplyStatus(StrEnum):
    pending = "pending"
    queued = "queued"
    applying = "applying"
    active = "active"
    failed = "failed"


class JobStatus(StrEnum):
    queued = "queued"
    applying = "applying"
    succeeded = "succeeded"
    failed = "failed"
    superseded = "superseded"


class JobType(StrEnum):
    service_update = "SERVICE_UPDATE"
    feed_sync = "FEED_SYNC"
    global_deny_apply = "GLOBAL_DENY_APPLY"


class ChangeTrigger(StrEnum):
    service = "service"
    plan = "plan"
    rule = "rule"
    whitelist = "whitelist"
    blacklist = "blacklist"
    enable = "enable"
    disable = "disable"
    feed_manual = "feed_manual"
    feed_schedule = "feed_schedule"
    feed_delete = "feed_delete"
    feed_dry_run = "feed_dry_run"
    global_deny_retry = "global_deny_retry"


class FeedFormat(StrEnum):
    line_list = "line_list"


class FeedSyncStatus(StrEnum):
    queued = "queued"
    running = "running"
    success = "success"
    partial = "partial"
    failed = "failed"


class ServiceMode(StrEnum):
    allow_rule_only = "allow-rule-only"


class Protocol(StrEnum):
    tcp = "tcp"
    udp = "udp"
    icmp = "icmp"
    any = "any"


class BlacklistScope(StrEnum):
    service = "service"
    global_ = "global"


class BlacklistSource(StrEnum):
    manual = "manual"
    feed = "feed"


class OveragePolicy(StrEnum):
    billed = "billed"
    capped = "capped"


class TelemetryScope(StrEnum):
    service = "service"
    node = "node"


class XdpMode(StrEnum):
    native = "native"
    generic = "generic"
    offline = "offline"
    unknown = "unknown"


role_enum = SAEnum(
    Role,
    name="role",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
user_status_enum = SAEnum(
    UserStatus,
    name="user_status",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
tenant_status_enum = SAEnum(
    TenantStatus,
    name="tenant_status",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
cidr_status_enum = SAEnum(
    CIDRStatus,
    name="cidr_status",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
apply_status_enum = SAEnum(
    ApplyStatus,
    name="apply_status",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
job_status_enum = SAEnum(
    JobStatus,
    name="job_status",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
job_type_enum = SAEnum(
    JobType,
    name="job_type",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
change_trigger_enum = SAEnum(
    ChangeTrigger,
    name="change_trigger",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
feed_format_enum = SAEnum(
    FeedFormat,
    name="feed_format",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
feed_sync_status_enum = SAEnum(
    FeedSyncStatus,
    name="feed_sync_status",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
service_mode_enum = SAEnum(
    ServiceMode,
    name="service_mode",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
protocol_enum = SAEnum(
    Protocol,
    name="protocol",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
blacklist_scope_enum = SAEnum(
    BlacklistScope,
    name="blacklist_scope",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
blacklist_source_enum = SAEnum(
    BlacklistSource,
    name="blacklist_source",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
overage_policy_enum = SAEnum(
    OveragePolicy,
    name="overage_policy",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
telemetry_scope_enum = SAEnum(
    TelemetryScope,
    name="telemetry_scope",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)
xdp_mode_enum = SAEnum(
    XdpMode,
    name="xdp_mode",
    native_enum=False,
    values_callable=lambda values: [value.value for value in values],
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("name", name="uq_tenants_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(CITEXT, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        tenant_status_enum,
        default=TenantStatus.active,
        nullable=False,
    )

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    allocations: Mapped[list["AllocatedCIDR"]] = relationship(
        back_populates="tenant",
        passive_deletes=True,
    )
    protected_services: Mapped[list["ProtectedService"]] = relationship(
        back_populates="tenant",
        passive_deletes=True,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(role = 'admin' AND tenant_id IS NULL) OR "
            "(role = 'tenant_user' AND tenant_id IS NOT NULL)",
            name="ck_users_role_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
    )
    role: Mapped[Role] = mapped_column(role_enum, nullable=False)
    username: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        user_status_enum,
        default=UserStatus.active,
        nullable=False,
    )
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant | None] = relationship(back_populates="users")
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="actor",
        passive_deletes=True,
    )
    allocated_cidrs: Mapped[list["AllocatedCIDR"]] = relationship(
        back_populates="allocator",
        passive_deletes=True,
    )
    created_services: Mapped[list["ProtectedService"]] = relationship(
        back_populates="creator",
        passive_deletes=True,
    )


class AllocatedCIDR(TimestampMixin, Base):
    __tablename__ = "allocated_cidr"
    __table_args__ = (
        ExcludeConstraint(
            ("cidr", "&&"),
            using="gist",
            where=text("status = 'active'"),
            ops={"cidr": "inet_ops"},
            name="allocated_cidr_active_no_overlap",
        ),
        Index(
            "ix_allocated_cidr_tenant_active",
            "tenant_id",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    status: Mapped[CIDRStatus] = mapped_column(
        cidr_status_enum,
        default=CIDRStatus.active,
        nullable=False,
    )
    allocated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="allocations")
    allocator: Mapped[User | None] = relationship(back_populates="allocated_cidrs")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_username: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    actor: Mapped[User | None] = relationship(back_populates="audit_events")

    def __init__(self, **kwargs: Any) -> None:
        if "metadata" in kwargs:
            kwargs["metadata_"] = kwargs.pop("metadata")
        super().__init__(**kwargs)


Index("ix_audit_events_created_at", AuditEvent.created_at)


class ProtectedService(TimestampMixin, Base):
    __tablename__ = "protected_service"
    __table_args__ = (
        ExcludeConstraint(
            ("cidr_or_ip", "&&"),
            using="gist",
            ops={"cidr_or_ip": "inet_ops"},
            name="protected_service_dest_no_overlap",
        ),
        Index("ix_protected_service_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dp_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        nullable=False,
        server_default=text("nextval('service_dp_id_seq')"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cidr_or_ip: Mapped[str] = mapped_column(CIDR, nullable=False)
    mode: Mapped[ServiceMode] = mapped_column(
        service_mode_enum,
        default=ServiceMode.allow_rule_only,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vip_pps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vip_bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    apply_status: Mapped[ApplyStatus] = mapped_column(
        apply_status_enum,
        default=ApplyStatus.pending,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="protected_services")
    creator: Mapped[User | None] = relationship(back_populates="created_services")
    plan: Mapped["ServicePlan | None"] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    rules: Mapped[list["AllowRule"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    whitelist_entries: Mapped[list["WhitelistEntry"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    blacklist_entries: Mapped[list["BlacklistEntry"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ServicePlan(TimestampMixin, Base):
    __tablename__ = "service_plan"
    __table_args__ = (
        CheckConstraint(
            "committed_clean_gbps >= 0 AND committed_clean_gbps <= ceiling_clean_gbps",
            name="ck_service_plan_committed_le_ceiling",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protected_service.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    committed_clean_gbps: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        default=Decimal("0"),
        nullable=False,
    )
    ceiling_clean_gbps: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        default=Decimal("0"),
        nullable=False,
    )
    billing_metric: Mapped[str] = mapped_column(
        String(64),
        default="p95_clean_bps",
        nullable=False,
    )
    overage_policy: Mapped[OveragePolicy] = mapped_column(
        overage_policy_enum,
        default=OveragePolicy.billed,
        nullable=False,
    )

    service: Mapped[ProtectedService] = relationship(back_populates="plan")


class AllowRule(TimestampMixin, Base):
    __tablename__ = "allow_rule"
    __table_args__ = (
        UniqueConstraint("service_id", "priority", name="uq_allow_rule_service_priority"),
        CheckConstraint(
            "(src_port_lo IS NULL AND src_port_hi IS NULL) OR "
            "(src_port_lo >= 0 AND src_port_lo <= 65535 AND "
            "src_port_hi >= 0 AND src_port_hi <= 65535 AND src_port_lo <= src_port_hi)",
            name="ck_allow_rule_src_port_range",
        ),
        CheckConstraint(
            "(dst_port_lo IS NULL AND dst_port_hi IS NULL) OR "
            "(dst_port_lo >= 0 AND dst_port_lo <= 65535 AND "
            "dst_port_hi >= 0 AND dst_port_hi <= 65535 AND dst_port_lo <= dst_port_hi)",
            name="ck_allow_rule_dst_port_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protected_service.id", ondelete="CASCADE"),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[Protocol] = mapped_column(protocol_enum, nullable=False)
    src_port_lo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    src_port_hi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dst_port_lo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dst_port_hi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    service: Mapped[ProtectedService] = relationship(back_populates="rules")


class WhitelistEntry(Base):
    __tablename__ = "whitelist_entry"
    __table_args__ = (
        UniqueConstraint("service_id", "source_cidr", name="uq_whitelist_service_source_cidr"),
        Index(
            "ix_whitelist_entry_source_cidr_gist",
            "source_cidr",
            postgresql_using="gist",
            postgresql_ops={"source_cidr": "inet_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protected_service.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    service: Mapped[ProtectedService] = relationship(back_populates="whitelist_entries")
    creator: Mapped[User | None] = relationship()


class BlacklistEntry(Base):
    __tablename__ = "blacklist_entry"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'service' AND service_id IS NOT NULL) OR "
            "(scope = 'global' AND service_id IS NULL)",
            name="ck_blacklist_scope_service_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protected_service.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope: Mapped[BlacklistScope] = mapped_column(blacklist_scope_enum, nullable=False)
    source: Mapped[BlacklistSource] = mapped_column(
        blacklist_source_enum,
        default=BlacklistSource.manual,
        nullable=False,
    )
    source_cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    service: Mapped[ProtectedService | None] = relationship(back_populates="blacklist_entries")
    creator: Mapped[User | None] = relationship()


class ThreatFeedSource(TimestampMixin, Base):
    __tablename__ = "threat_feed_source"
    __table_args__ = (
        UniqueConstraint("name", name="uq_threat_feed_source_name"),
        CheckConstraint(
            "sync_interval_seconds >= 300 AND sync_interval_seconds <= 604800",
            name="ck_threat_feed_source_sync_interval",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(CITEXT, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[FeedFormat] = mapped_column(
        feed_format_enum,
        default=FeedFormat.line_list,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_env_var: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sync_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_status: Mapped[FeedSyncStatus | None] = mapped_column(feed_sync_status_enum, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sync_runs: Mapped[list["FeedSyncRun"]] = relationship(passive_deletes=True)
    assertions: Mapped[list["FeedBlacklistAssertion"]] = relationship(passive_deletes=True)


class FeedSyncRun(Base):
    __tablename__ = "feed_sync_run"
    __table_args__ = (
        UniqueConstraint("feed_source_id", "sequence", name="uq_feed_sync_run_source_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feed_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threat_feed_source.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trigger: Mapped[ChangeTrigger] = mapped_column(change_trigger_enum, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[FeedSyncStatus] = mapped_column(
        feed_sync_status_enum,
        default=FeedSyncStatus.queued,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_lines: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    removed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_invalid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overlap_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    global_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    desired_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    node_map_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    source: Mapped[ThreatFeedSource] = relationship(back_populates="sync_runs")
    overlaps: Mapped[list["FeedSyncOverlap"]] = relationship(passive_deletes=True)


class FeedBlacklistAssertion(Base):
    __tablename__ = "feed_blacklist_assertion"

    feed_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threat_feed_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    blacklist_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blacklist_entry.id", ondelete="CASCADE"),
        primary_key=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source: Mapped[ThreatFeedSource] = relationship(back_populates="assertions")
    blacklist_entry: Mapped[BlacklistEntry] = relationship()


Index("ix_feed_blacklist_assertion_blacklist_entry_id", FeedBlacklistAssertion.blacklist_entry_id)


class FeedSyncOverlap(Base):
    __tablename__ = "feed_sync_overlap"
    __table_args__ = (
        UniqueConstraint(
            "feed_sync_run_id",
            "feed_source_cidr",
            "whitelist_entry_id",
            name="uq_feed_sync_overlap_run_cidr_whitelist",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feed_sync_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feed_sync_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    feed_source_cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    whitelist_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whitelist_entry.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    run: Mapped[FeedSyncRun] = relationship(back_populates="overlaps")
    whitelist_entry: Mapped[WhitelistEntry] = relationship()


class GlobalDenyState(Base):
    __tablename__ = "global_deny_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_global_deny_state_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    desired_revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    active_revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    desired_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    apply_status: Mapped[ApplyStatus] = mapped_column(
        apply_status_enum,
        default=ApplyStatus.pending,
        nullable=False,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_node_map_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class AgentJob(Base):
    __tablename__ = "agent_job"
    __table_args__ = (
        CheckConstraint(
            "(job_type = 'SERVICE_UPDATE' AND target_type = 'service' "
            "AND target_id IS NOT NULL AND feed_sync_run_id IS NULL) OR "
            "(job_type = 'FEED_SYNC' AND target_type = 'feed_sync_run' "
            "AND target_id IS NULL AND feed_sync_run_id IS NOT NULL) OR "
            "(job_type = 'GLOBAL_DENY_APPLY' AND target_type = 'global_deny' "
            "AND target_id IS NULL AND feed_sync_run_id IS NULL)",
            name="ck_agent_job_target_shape",
        ),
        Index("ix_agent_job_status", "status"),
        Index("ix_agent_job_target", "target_type", "target_id"),
        Index(
            "uq_agent_job_service_target_version",
            "target_id",
            "version",
            unique=True,
            postgresql_where=text("job_type = 'SERVICE_UPDATE'"),
        ),
        Index(
            "uq_agent_job_feed_sync_run",
            "feed_sync_run_id",
            unique=True,
            postgresql_where=text("job_type = 'FEED_SYNC'"),
        ),
        Index(
            "uq_agent_job_global_deny_revision",
            "version",
            unique=True,
            postgresql_where=text("job_type = 'GLOBAL_DENY_APPLY'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protected_service.id", ondelete="CASCADE"),
        nullable=True,
    )
    feed_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feed_sync_run.id", ondelete="CASCADE"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    job_type: Mapped[JobType] = mapped_column(
        job_type_enum,
        default=JobType.service_update,
        nullable=False,
    )
    trigger: Mapped[ChangeTrigger] = mapped_column(change_trigger_enum, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum,
        default=JobStatus.queued,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    service: Mapped[ProtectedService] = relationship()
    feed_sync_run: Mapped[FeedSyncRun | None] = relationship()


Index(
    "uq_protected_service_tenant_lower_name",
    ProtectedService.tenant_id,
    func.lower(ProtectedService.name),
    unique=True,
)
Index(
    "uq_blacklist_service_source_cidr",
    BlacklistEntry.service_id,
    BlacklistEntry.source_cidr,
    unique=True,
    postgresql_where=text("scope = 'service'"),
)
Index(
    "uq_blacklist_global_source_cidr",
    BlacklistEntry.source_cidr,
    unique=True,
    postgresql_where=text("scope = 'global'"),
)


class TelemetryCounter(Base):
    __tablename__ = "telemetry_counter"
    __table_args__ = (
        Index(
            "ix_telemetry_counter_scope_service_window_start",
            "scope",
            "service_id",
            text("window_start DESC"),
        ),
        Index(
            "ix_telemetry_counter_scope_window_start",
            "scope",
            text("window_start DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[TelemetryScope] = mapped_column(telemetry_scope_enum, nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("protected_service.id", ondelete="SET NULL"),
        nullable=True,
    )
    dp_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    clean_pkts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    clean_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    drop_pkts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    drop_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    drop_by_reason: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, nullable=False)
    pps: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bps: Mapped[int] = mapped_column(BigInteger, nullable=False)
    top_dst_ports: Mapped[list[dict[str, int]] | None] = mapped_column(JSONB, nullable=True)
    top_src: Mapped[list[dict[str, int | str]] | None] = mapped_column(JSONB, nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    service: Mapped[ProtectedService | None] = relationship()


class NodeHealthSnapshot(Base):
    __tablename__ = "node_health_snapshot"
    __table_args__ = (Index("ix_node_health_snapshot_captured_at", text("captured_at DESC")),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    xdp_mode: Mapped[XdpMode] = mapped_column(xdp_mode_enum, nullable=False)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    map_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    map_error_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_clean_bps: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_capacity_bps: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bloom_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
