"""add node control desired state

Revision ID: 20260714_0010
Revises: 20260714_0009
Create Date: 2026-07-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0010"
down_revision: str | None = "20260714_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_control",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("bypass_enabled", sa.Boolean(), nullable=False),
        sa.Column("maintenance_enabled", sa.Boolean(), nullable=False),
        sa.Column("bypass_reason", sa.String(length=512), nullable=True),
        sa.Column("bypass_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maintenance_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bypass_actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("maintenance_actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_node_control_singleton"),
        sa.ForeignKeyConstraint(["bypass_actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["maintenance_actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("node_control")
