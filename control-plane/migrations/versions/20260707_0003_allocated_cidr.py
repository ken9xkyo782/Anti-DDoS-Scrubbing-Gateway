"""allocated cidr

Revision ID: 20260707_0003
Revises: 20260707_0002
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260707_0003"
down_revision: str | None = "20260707_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tenants",
        "name",
        existing_type=sa.String(length=255),
        type_=postgresql.CITEXT(),
        existing_nullable=False,
        postgresql_using="name::citext",
    )
    op.create_unique_constraint("uq_tenants_name", "tenants", ["name"])
    op.create_table(
        "allocated_cidr",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cidr", postgresql.CIDR(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "revoked", name="cidr_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("allocated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["allocated_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        ALTER TABLE allocated_cidr
          ADD CONSTRAINT allocated_cidr_active_no_overlap
          EXCLUDE USING gist (cidr inet_ops WITH &&)
          WHERE (status = 'active')
        """
    )
    op.create_index(
        "ix_allocated_cidr_tenant_active",
        "allocated_cidr",
        ["tenant_id"],
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ix_allocated_cidr_tenant_active", table_name="allocated_cidr")
    op.drop_constraint(
        "allocated_cidr_active_no_overlap",
        "allocated_cidr",
        type_="exclude",
    )
    op.drop_table("allocated_cidr")
    op.drop_constraint("uq_tenants_name", "tenants", type_="unique")
    op.alter_column(
        "tenants",
        "name",
        existing_type=postgresql.CITEXT(),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
