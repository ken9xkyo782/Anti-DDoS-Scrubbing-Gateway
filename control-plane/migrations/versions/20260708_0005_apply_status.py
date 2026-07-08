"""apply status

Revision ID: 20260708_0005
Revises: 20260707_0004
Create Date: 2026-07-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260708_0005"
down_revision: str | None = "20260707_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "job_type",
            sa.Enum("SERVICE_UPDATE", name="job_type", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "trigger",
            sa.Enum(
                "service",
                "plan",
                "rule",
                "whitelist",
                "blacklist",
                "enable",
                "disable",
                name="change_trigger",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "applying",
                "succeeded",
                "failed",
                "superseded",
                name="job_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["target_id"], ["protected_service.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_type",
            "target_id",
            "version",
            name="agent_job_target_version_unique",
        ),
    )
    op.create_index("ix_agent_job_status", "agent_job", ["status"])
    op.create_index("ix_agent_job_target", "agent_job", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_job_target", table_name="agent_job")
    op.drop_index("ix_agent_job_status", table_name="agent_job")
    op.drop_table("agent_job")
