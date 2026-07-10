from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _truncate_tables(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE audit_events, global_deny_state, feed_sync_overlap, "
                "feed_blacklist_assertion, agent_job, feed_sync_run, threat_feed_source, "
                "blacklist_entry, whitelist_entry, allow_rule, service_plan, protected_service, "
                "allocated_cidr, users, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


@pytest.fixture
async def committed_db(migrated_db: None) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Provide real transaction commits with deterministic database cleanup."""
    _ = migrated_db

    from app.db.session import dispose_engine, get_session_factory

    await dispose_engine()
    session_factory = get_session_factory()
    await _truncate_tables(session_factory)
    try:
        yield session_factory
    finally:
        await _truncate_tables(session_factory)
        await dispose_engine()
