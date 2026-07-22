import asyncio

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BlockedUdpPort, Role, User
from app.db.session import dispose_engine, get_session_factory

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession) -> User:
    actor = User(
        username="blocked-port-actor",
        role=Role.admin,
        password_hash="$argon2id$hash",
    )
    db_session.add(actor)
    await db_session.flush()
    return actor


async def test_blocked_udp_port_insert_and_defaults(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    entry = BlockedUdpPort(port=123, note="NTP amplification", created_by=actor.id)
    db_session.add(entry)
    await db_session.flush()
    await db_session.refresh(entry)

    assert entry.port == 123
    assert entry.note == "NTP amplification"
    assert entry.created_by == actor.id
    assert entry.created_at is not None


async def test_blocked_udp_port_duplicate_integrity_error(db_session: AsyncSession) -> None:
    db_session.add(BlockedUdpPort(port=123, note="first"))
    await db_session.flush()

    db_session.add(BlockedUdpPort(port=123, note="duplicate"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.parametrize("invalid_port", [-1, 70000])
async def test_blocked_udp_port_range_check_constraint(
    db_session: AsyncSession, invalid_port: int
) -> None:
    db_session.add(BlockedUdpPort(port=invalid_port, note="invalid port"))
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "ck_blocked_udp_port_range" in str(exc_info.value)


async def test_deleting_user_nulls_created_by(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    entry = BlockedUdpPort(port=1900, created_by=actor.id)
    db_session.add(entry)
    await db_session.flush()

    await db_session.delete(actor)
    await db_session.flush()
    await db_session.refresh(entry)

    assert entry.created_by is None


async def test_blocked_udp_port_migration_upgrades_and_downgrades_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    del committed_db
    config = Config("alembic.ini")
    await dispose_engine()
    await asyncio.to_thread(downgrade, config, "20260721_0012")
    try:
        session_factory = get_session_factory()
        async with session_factory() as db_session:
            table_before_upgrade = await db_session.scalar(
                text("SELECT to_regclass('public.blocked_udp_port')")
            )

        assert table_before_upgrade is None

        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")

        session_factory = get_session_factory()
        async with session_factory() as db_session:
            table_after_upgrade = await db_session.scalar(
                text("SELECT to_regclass('public.blocked_udp_port')")
            )

        assert table_after_upgrade == "blocked_udp_port"
    finally:
        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")
