import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant
from app.db.session import add_post_commit_callback, get_session_factory, session_scope


async def test_async_session_executes_select(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


async def test_session_scope_runs_post_commit_callback(db_session: AsyncSession) -> None:
    callback_ran = False

    async def callback() -> None:
        nonlocal callback_ran
        callback_ran = True

    async with session_scope() as session:
        add_post_commit_callback(session, callback)

    assert callback_ran


async def test_session_scope_rolls_back_and_discards_post_commit_callback(
    db_session: AsyncSession,
) -> None:
    callback_ran = False
    tenant_id = uuid.uuid4()

    async def callback() -> None:
        nonlocal callback_ran
        callback_ran = True

    with pytest.raises(RuntimeError, match="scope failed"):
        async with session_scope() as session:
            session.add(Tenant(id=tenant_id, name=f"session-scope-rollback-{tenant_id}"))
            await session.flush()
            add_post_commit_callback(session, callback)
            raise RuntimeError("scope failed")

    assert not callback_ran
    async with get_session_factory()() as session:
        assert await session.get(Tenant, tenant_id) is None


async def test_session_scope_commits_row_visible_in_fresh_session(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()

    try:
        async with session_scope() as session:
            session.add(Tenant(id=tenant_id, name=f"session-scope-commit-{tenant_id}"))

        async with get_session_factory()() as session:
            tenant = await session.get(Tenant, tenant_id)

        assert tenant is not None
        assert tenant.id == tenant_id
    finally:
        async with get_session_factory()() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is not None:
                await session.delete(tenant)
                await session.commit()
