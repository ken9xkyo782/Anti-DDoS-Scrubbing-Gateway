"""add alerting persistence models

Revision ID: 20260714_0011
Revises: 20260714_0010
Create Date: 2026-07-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0011"
down_revision: str | None = "20260714_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "severity_override",
            sa.Enum(
                "info",
                "warning",
                "critical",
                name="alert_severity",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("fire_threshold_override", sa.Numeric(18, 4), nullable=True),
        sa.Column("clear_threshold_override", sa.Numeric(18, 4), nullable=True),
        sa.Column("silence_in_maintenance", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "notification_channel",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("email", "webhook", name="channel_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "min_severity",
            sa.Enum(
                "info",
                "warning",
                "critical",
                name="alert_severity",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_channel_tenant",
        "notification_channel",
        ["tenant_id", "enabled"],
    )
    op.create_table(
        "alert",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_key", sa.String(length=64), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("node", "service", name="alert_scope", native_enum=False),
            nullable=False,
        ),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("service_name", sa.String(length=255), nullable=True),
        sa.Column(
            "severity",
            sa.Enum(
                "info",
                "warning",
                "critical",
                name="alert_severity",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "firing",
                "resolved",
                name="alert_state",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("metric_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fire_streak", sa.Integer(), nullable=False),
        sa.Column("clear_streak", sa.Integer(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_alert_active_scope",
        "alert",
        ["rule_key", "scope_key"],
        unique=True,
        postgresql_where=sa.text("state <> 'resolved'"),
    )
    op.create_index("ix_alert_tenant_state", "alert", ["tenant_id", "state"])
    op.create_index("ix_alert_state_fired", "alert", ["state", "fired_at"])
    op.create_table(
        "alert_notification",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_name", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("email", "webhook", name="channel_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "sent",
                "retrying",
                "failed",
                name="notification_state",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["notification_channel.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_notification_alert", "alert_notification", ["alert_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_notification_alert", table_name="alert_notification")
    op.drop_table("alert_notification")
    op.drop_index("ix_alert_state_fired", table_name="alert")
    op.drop_index("ix_alert_tenant_state", table_name="alert")
    op.drop_index("uq_alert_active_scope", table_name="alert")
    op.drop_table("alert")
    op.drop_index("ix_notification_channel_tenant", table_name="notification_channel")
    op.drop_table("notification_channel")
    op.drop_table("alert_rule")
