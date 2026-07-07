import uuid
from datetime import UTC, datetime
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
    committed_clean_gbps: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    ceiling_clean_gbps: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
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
