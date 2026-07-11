"""add protected service data-plane surrogate

Revision ID: 20260710_0007
Revises: 20260710_0006
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0007"
down_revision: str | None = "20260710_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE service_dp_id_seq START WITH 1")
    op.add_column("protected_service", sa.Column("dp_id", sa.Integer(), nullable=True))
    op.execute("UPDATE protected_service SET dp_id = nextval('service_dp_id_seq')")
    op.alter_column(
        "protected_service",
        "dp_id",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("nextval('service_dp_id_seq')"),
    )
    op.create_unique_constraint("uq_protected_service_dp_id", "protected_service", ["dp_id"])


def downgrade() -> None:
    op.drop_constraint("uq_protected_service_dp_id", "protected_service", type_="unique")
    op.drop_column("protected_service", "dp_id")
    op.execute("DROP SEQUENCE service_dp_id_seq")
