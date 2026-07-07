import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        tenant_status_enum,
        default=TenantStatus.active,
        nullable=False,
    )

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


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
