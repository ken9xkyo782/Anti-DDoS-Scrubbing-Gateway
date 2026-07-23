"""remove service blacklist

Revision ID: 20260723_0014
Revises: 20260722_0013
Create Date: 2026-07-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0014"
down_revision: str | None = "20260722_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM blacklist_entry WHERE scope = 'service' OR service_id IS NOT NULL")
    op.drop_constraint("ck_blacklist_scope_service_id", "blacklist_entry", type_="check")
    op.drop_index("uq_blacklist_service_source_cidr", table_name="blacklist_entry")
    op.drop_column("blacklist_entry", "service_id")


def downgrade() -> None:
    op.add_column(
        "blacklist_entry",
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("protected_service.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_blacklist_service_source_cidr",
        "blacklist_entry",
        ["service_id", "source_cidr"],
        unique=True,
        postgresql_where=sa.text("scope = 'service'"),
    )
    op.create_check_constraint(
        "ck_blacklist_scope_service_id",
        "blacklist_entry",
        "(scope = 'service' AND service_id IS NOT NULL) OR "
        "(scope = 'global' AND service_id IS NULL)",
    )
