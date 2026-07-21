"""add service rate limits and remove rule rate limits

Revision ID: 20260721_0012
Revises: 20260714_0011
Create Date: 2026-07-21 09:42:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0012"
down_revision: str | None = "20260714_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add service_pps and service_bps to protected_service
    op.add_column(
        "protected_service",
        sa.Column("service_pps", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "protected_service",
        sa.Column("service_bps", sa.BigInteger(), nullable=True),
    )
    # 2. Drop pps and bps from allow_rule
    op.drop_column("allow_rule", "pps")
    op.drop_column("allow_rule", "bps")


def downgrade() -> None:
    # 1. Add pps and bps back to allow_rule
    op.add_column(
        "allow_rule",
        sa.Column("pps", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "allow_rule",
        sa.Column("bps", sa.BigInteger(), nullable=True),
    )
    # 2. Drop service_pps and service_bps from protected_service
    op.drop_column("protected_service", "service_pps")
    op.drop_column("protected_service", "service_bps")
