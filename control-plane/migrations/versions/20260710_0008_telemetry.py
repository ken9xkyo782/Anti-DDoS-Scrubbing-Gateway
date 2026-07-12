"""add telemetry persistence models

Revision ID: 20260710_0008
Revises: 20260710_0007
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260710_0008"
down_revision: str | None = "20260710_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telemetry_counter",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("service", "node", name="telemetry_scope", native_enum=False),
            nullable=False,
        ),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dp_id", sa.Integer(), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("clean_pkts", sa.BigInteger(), nullable=False),
        sa.Column("clean_bytes", sa.BigInteger(), nullable=False),
        sa.Column("drop_pkts", sa.BigInteger(), nullable=False),
        sa.Column("drop_bytes", sa.BigInteger(), nullable=False),
        sa.Column("drop_by_reason", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pps", sa.BigInteger(), nullable=False),
        sa.Column("bps", sa.BigInteger(), nullable=False),
        sa.Column("top_dst_ports", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("top_src", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_baseline", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_telemetry_counter_scope_service_window_start",
        "telemetry_counter",
        ["scope", "service_id", sa.text("window_start DESC")],
    )
    op.create_index(
        "ix_telemetry_counter_scope_window_start",
        "telemetry_counter",
        ["scope", sa.text("window_start DESC")],
    )

    op.create_table(
        "node_health_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "xdp_mode",
            sa.Enum(
                "native",
                "generic",
                "offline",
                "unknown",
                name="xdp_mode",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("map_version", sa.BigInteger(), nullable=True),
        sa.Column("map_error_count", sa.BigInteger(), nullable=False),
        sa.Column("node_clean_bps", sa.BigInteger(), nullable=False),
        sa.Column("node_capacity_bps", sa.BigInteger(), nullable=False),
        sa.Column("bloom_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_node_health_snapshot_captured_at",
        "node_health_snapshot",
        [sa.text("captured_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_node_health_snapshot_captured_at", table_name="node_health_snapshot")
    op.drop_table("node_health_snapshot")
    op.drop_index("ix_telemetry_counter_scope_window_start", table_name="telemetry_counter")
    op.drop_index(
        "ix_telemetry_counter_scope_service_window_start",
        table_name="telemetry_counter",
    )
    op.drop_table("telemetry_counter")
