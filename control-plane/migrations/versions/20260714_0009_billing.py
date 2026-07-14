"""add billing persistence models

Revision ID: 20260714_0009
Revises: 20260710_0008
Create Date: 2026-07-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0009"
down_revision: str | None = "20260710_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_sample",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dp_id", sa.Integer(), nullable=True),
        sa.Column("sample_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clean_bps", sa.BigInteger(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("is_reset", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "sample_ts", name="uq_billing_sample_service_ts"),
    )
    op.create_index(
        "ix_billing_sample_service_ts",
        "billing_sample",
        ["service_id", "sample_ts"],
    )

    op.create_table(
        "billing_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("service_name", sa.String(length=255), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("billing_metric", sa.String(length=64), nullable=False),
        sa.Column("committed_clean_gbps", sa.Numeric(10, 2), nullable=False),
        sa.Column("p95_clean_gbps", sa.Numeric(10, 2), nullable=False),
        sa.Column("billed_gbps", sa.Numeric(10, 2), nullable=False),
        sa.Column("overage_gbps", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "overage_policy",
            sa.Enum("billed", "capped", name="overage_policy", native_enum=False),
            nullable=False,
        ),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "final", name="billing_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "period_start", name="uq_billing_usage_service_period"),
    )
    op.create_index(
        "ix_billing_usage_tenant_period",
        "billing_usage",
        ["tenant_id", "period_start"],
    )
    op.create_index(
        "ix_billing_usage_status_end",
        "billing_usage",
        ["status", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_billing_usage_status_end", table_name="billing_usage")
    op.drop_index("ix_billing_usage_tenant_period", table_name="billing_usage")
    op.drop_table("billing_usage")
    op.drop_index("ix_billing_sample_service_ts", table_name="billing_sample")
    op.drop_table("billing_sample")
