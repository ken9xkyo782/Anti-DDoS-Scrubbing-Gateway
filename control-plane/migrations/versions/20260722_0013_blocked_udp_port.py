"""add blocked_udp_port model

Revision ID: 20260722_0013
Revises: 20260721_0012
Create Date: 2026-07-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0013"
down_revision: str | None = "20260721_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blocked_udp_port",
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("port >= 0 AND port <= 65535", name="ck_blocked_udp_port_range"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("port"),
    )


def downgrade() -> None:
    op.drop_table("blocked_udp_port")
