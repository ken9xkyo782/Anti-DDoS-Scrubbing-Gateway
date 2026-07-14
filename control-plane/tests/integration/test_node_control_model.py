import asyncio

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import NodeControl, Role, User
from app.db.session import dispose_engine, get_session_factory

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession) -> User:
    actor = User(
        username="node-control-actor",
        role=Role.admin,
        password_hash="$argon2id$hash",
    )
    db_session.add(actor)
    await db_session.flush()
    return actor


async def test_node_control_defaults_to_disabled_singleton_state(db_session: AsyncSession) -> None:
    control = NodeControl()
    db_session.add(control)
    await db_session.flush()
    await db_session.refresh(control)

    assert control.id == 1
    assert control.bypass_enabled is False
    assert control.maintenance_enabled is False
    assert control.bypass_reason is None
    assert control.bypass_activated_at is None
    assert control.maintenance_activated_at is None
    assert control.bypass_actor_user_id is None
    assert control.maintenance_actor_user_id is None
    assert control.created_at is not None
    assert control.updated_at is not None


async def test_node_control_rejects_non_singleton_id(db_session: AsyncSession) -> None:
    db_session.add(NodeControl(id=2))

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "ck_node_control_singleton" in str(exc_info.value)


async def test_deleting_actor_nulls_node_control_actor_references(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    control = NodeControl(
        bypass_actor_user_id=actor.id,
        maintenance_actor_user_id=actor.id,
    )
    db_session.add(control)
    await db_session.flush()

    await db_session.delete(actor)
    await db_session.flush()
    await db_session.refresh(control)

    assert control.bypass_actor_user_id is None
    assert control.maintenance_actor_user_id is None


async def test_node_control_migration_upgrades_and_downgrades_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    del committed_db
    config = Config("alembic.ini")
    await dispose_engine()
    await asyncio.to_thread(downgrade, config, "20260714_0009")
    try:
        session_factory = get_session_factory()
        async with session_factory() as db_session:
            table_before_upgrade = await db_session.scalar(
                text("SELECT to_regclass('public.node_control')")
            )

        assert table_before_upgrade is None

        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")

        session_factory = get_session_factory()
        async with session_factory() as db_session:
            table_after_upgrade = await db_session.scalar(
                text("SELECT to_regclass('public.node_control')")
            )

        assert table_after_upgrade == "node_control"
    finally:
        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")
