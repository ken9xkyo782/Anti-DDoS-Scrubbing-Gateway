"""service rule list

Revision ID: 20260707_0004
Revises: 20260707_0003
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260707_0004"
down_revision: str | None = "20260707_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protected_service",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("cidr_or_ip", postgresql.CIDR(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("allow-rule-only", name="service_mode", native_enum=False),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("vip_pps", sa.BigInteger(), nullable=True),
        sa.Column("vip_bps", sa.BigInteger(), nullable=True),
        sa.Column(
            "apply_status",
            sa.Enum(
                "pending",
                "queued",
                "applying",
                "active",
                "failed",
                name="apply_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active_version", sa.Integer(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        ALTER TABLE protected_service
          ADD CONSTRAINT protected_service_dest_no_overlap
          EXCLUDE USING gist (cidr_or_ip inet_ops WITH &&)
        """
    )
    op.create_index("ix_protected_service_tenant", "protected_service", ["tenant_id"])
    op.create_index(
        "uq_protected_service_tenant_lower_name",
        "protected_service",
        ["tenant_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "service_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("committed_clean_gbps", sa.Numeric(10, 2), nullable=False),
        sa.Column("ceiling_clean_gbps", sa.Numeric(10, 2), nullable=False),
        sa.Column("billing_metric", sa.String(length=64), nullable=False),
        sa.Column(
            "overage_policy",
            sa.Enum("billed", "capped", name="overage_policy", native_enum=False),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "committed_clean_gbps >= 0 AND committed_clean_gbps <= ceiling_clean_gbps",
            name="ck_service_plan_committed_le_ceiling",
        ),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id"),
    )

    op.create_table(
        "allow_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "protocol",
            sa.Enum("tcp", "udp", "icmp", "any", name="protocol", native_enum=False),
            nullable=False,
        ),
        sa.Column("src_port_lo", sa.Integer(), nullable=True),
        sa.Column("src_port_hi", sa.Integer(), nullable=True),
        sa.Column("dst_port_lo", sa.Integer(), nullable=True),
        sa.Column("dst_port_hi", sa.Integer(), nullable=True),
        sa.Column("pps", sa.BigInteger(), nullable=True),
        sa.Column("bps", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(src_port_lo IS NULL AND src_port_hi IS NULL) OR "
            "(src_port_lo >= 0 AND src_port_lo <= 65535 AND "
            "src_port_hi >= 0 AND src_port_hi <= 65535 AND src_port_lo <= src_port_hi)",
            name="ck_allow_rule_src_port_range",
        ),
        sa.CheckConstraint(
            "(dst_port_lo IS NULL AND dst_port_hi IS NULL) OR "
            "(dst_port_lo >= 0 AND dst_port_lo <= 65535 AND "
            "dst_port_hi >= 0 AND dst_port_hi <= 65535 AND dst_port_lo <= dst_port_hi)",
            name="ck_allow_rule_dst_port_range",
        ),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "priority", name="uq_allow_rule_service_priority"),
    )

    op.create_table(
        "whitelist_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_cidr", postgresql.CIDR(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "source_cidr", name="uq_whitelist_service_source_cidr"),
    )

    op.create_table(
        "blacklist_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "scope",
            sa.Enum("service", "global", name="blacklist_scope", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum("manual", "feed", name="blacklist_source", native_enum=False),
            nullable=False,
        ),
        sa.Column("source_cidr", postgresql.CIDR(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'service' AND service_id IS NOT NULL) OR "
            "(scope = 'global' AND service_id IS NULL)",
            name="ck_blacklist_scope_service_id",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_id"], ["protected_service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_blacklist_service_source_cidr",
        "blacklist_entry",
        ["service_id", "source_cidr"],
        unique=True,
        postgresql_where=sa.text("scope = 'service'"),
    )
    op.create_index(
        "uq_blacklist_global_source_cidr",
        "blacklist_entry",
        ["source_cidr"],
        unique=True,
        postgresql_where=sa.text("scope = 'global'"),
    )


def downgrade() -> None:
    op.drop_index("uq_blacklist_global_source_cidr", table_name="blacklist_entry")
    op.drop_index("uq_blacklist_service_source_cidr", table_name="blacklist_entry")
    op.drop_table("blacklist_entry")
    op.drop_table("whitelist_entry")
    op.drop_table("allow_rule")
    op.drop_table("service_plan")
    op.drop_index("uq_protected_service_tenant_lower_name", table_name="protected_service")
    op.drop_index("ix_protected_service_tenant", table_name="protected_service")
    op.drop_constraint(
        "protected_service_dest_no_overlap",
        "protected_service",
        type_="exclude",
    )
    op.drop_table("protected_service")
