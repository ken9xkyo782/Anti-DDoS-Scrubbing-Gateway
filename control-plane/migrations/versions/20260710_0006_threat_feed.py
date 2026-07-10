"""threat feed persistence

Revision ID: 20260710_0006
Revises: 20260708_0005
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260710_0006"
down_revision: str | None = "20260708_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "threat_feed_source",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", postgresql.CITEXT(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "format",
            sa.Enum("line_list", name="feed_format", native_enum=False),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("credential_env_var", sa.String(length=128), nullable=True),
        sa.Column("sync_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "last_status",
            sa.Enum(
                "queued",
                "running",
                "success",
                "partial",
                "failed",
                name="feed_sync_status",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sync_interval_seconds >= 300 AND sync_interval_seconds <= 604800",
            name="ck_threat_feed_source_sync_interval",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_threat_feed_source_name"),
    )
    op.create_table(
        "feed_sync_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feed_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
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
                "feed_manual",
                "feed_schedule",
                "feed_delete",
                "feed_dry_run",
                "global_deny_retry",
                name="change_trigger",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "success",
                "partial",
                "failed",
                name="feed_sync_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fetched_lines", sa.Integer(), nullable=False),
        sa.Column("valid", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("added", sa.Integer(), nullable=False),
        sa.Column("removed", sa.Integer(), nullable=False),
        sa.Column("skipped_invalid", sa.Integer(), nullable=False),
        sa.Column("overlap_count", sa.Integer(), nullable=False),
        sa.Column("global_changed", sa.Boolean(), nullable=False),
        sa.Column("desired_revision", sa.BigInteger(), nullable=True),
        sa.Column("node_map_version", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["feed_source_id"], ["threat_feed_source.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_source_id", "sequence", name="uq_feed_sync_run_source_sequence"),
    )
    op.create_table(
        "feed_blacklist_assertion",
        sa.Column("feed_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blacklist_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["blacklist_entry_id"], ["blacklist_entry.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feed_source_id"], ["threat_feed_source.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("feed_source_id", "blacklist_entry_id"),
    )
    op.create_index(
        "ix_feed_blacklist_assertion_blacklist_entry_id",
        "feed_blacklist_assertion",
        ["blacklist_entry_id"],
    )
    op.create_table(
        "feed_sync_overlap",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feed_sync_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feed_source_cidr", postgresql.CIDR(), nullable=False),
        sa.Column("whitelist_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feed_sync_run_id"], ["feed_sync_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["whitelist_entry_id"], ["whitelist_entry.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "feed_sync_run_id",
            "feed_source_cidr",
            "whitelist_entry_id",
            name="uq_feed_sync_overlap_run_cidr_whitelist",
        ),
    )
    op.create_table(
        "global_deny_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("desired_revision", sa.BigInteger(), nullable=False),
        sa.Column("active_revision", sa.BigInteger(), nullable=False),
        sa.Column("desired_digest", sa.String(length=64), nullable=True),
        sa.Column("active_digest", sa.String(length=64), nullable=True),
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
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_node_map_version", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_global_deny_state_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_whitelist_entry_source_cidr_gist",
        "whitelist_entry",
        ["source_cidr"],
        postgresql_using="gist",
        postgresql_ops={"source_cidr": "inet_ops"},
    )

    op.add_column(
        "agent_job",
        sa.Column("feed_sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column(
        "agent_job",
        "target_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "agent_job",
        "job_type",
        existing_type=sa.String(length=14),
        type_=sa.String(length=17),
        existing_nullable=False,
    )
    op.alter_column(
        "agent_job",
        "trigger",
        existing_type=sa.String(length=9),
        type_=sa.String(length=17),
        existing_nullable=False,
    )
    op.drop_constraint("agent_job_target_version_unique", "agent_job", type_="unique")
    op.create_foreign_key(
        "agent_job_feed_sync_run_id_fkey",
        "agent_job",
        "feed_sync_run",
        ["feed_sync_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_agent_job_target_shape",
        "agent_job",
        "(job_type = 'SERVICE_UPDATE' AND target_type = 'service' "
        "AND target_id IS NOT NULL AND feed_sync_run_id IS NULL) OR "
        "(job_type = 'FEED_SYNC' AND target_type = 'feed_sync_run' "
        "AND target_id IS NULL AND feed_sync_run_id IS NOT NULL) OR "
        "(job_type = 'GLOBAL_DENY_APPLY' AND target_type = 'global_deny' "
        "AND target_id IS NULL AND feed_sync_run_id IS NULL)",
    )
    op.create_index(
        "uq_agent_job_service_target_version",
        "agent_job",
        ["target_id", "version"],
        unique=True,
        postgresql_where=sa.text("job_type = 'SERVICE_UPDATE'"),
    )
    op.create_index(
        "uq_agent_job_feed_sync_run",
        "agent_job",
        ["feed_sync_run_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'FEED_SYNC'"),
    )
    op.create_index(
        "uq_agent_job_global_deny_revision",
        "agent_job",
        ["version"],
        unique=True,
        postgresql_where=sa.text("job_type = 'GLOBAL_DENY_APPLY'"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_job_global_deny_revision", table_name="agent_job")
    op.drop_index("uq_agent_job_feed_sync_run", table_name="agent_job")
    op.drop_index("uq_agent_job_service_target_version", table_name="agent_job")
    op.drop_constraint("ck_agent_job_target_shape", "agent_job", type_="check")
    op.drop_constraint("agent_job_feed_sync_run_id_fkey", "agent_job", type_="foreignkey")
    op.drop_column("agent_job", "feed_sync_run_id")
    op.execute("DELETE FROM agent_job WHERE job_type <> 'SERVICE_UPDATE'")
    op.alter_column(
        "agent_job",
        "target_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "agent_job",
        "job_type",
        existing_type=sa.String(length=17),
        type_=sa.String(length=14),
        existing_nullable=False,
    )
    op.alter_column(
        "agent_job",
        "trigger",
        existing_type=sa.String(length=17),
        type_=sa.String(length=9),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        "agent_job_target_version_unique",
        "agent_job",
        ["target_type", "target_id", "version"],
    )

    op.drop_index("ix_whitelist_entry_source_cidr_gist", table_name="whitelist_entry")
    op.drop_table("global_deny_state")
    op.drop_table("feed_sync_overlap")
    op.drop_index(
        "ix_feed_blacklist_assertion_blacklist_entry_id",
        table_name="feed_blacklist_assertion",
    )
    op.drop_table("feed_blacklist_assertion")
    op.drop_table("feed_sync_run")
    op.drop_table("threat_feed_source")
