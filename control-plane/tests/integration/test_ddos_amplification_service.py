import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, Role, User
from app.services.ddos_amplification import (
    HARDCODED_AMP_PORTS,
    add_blocked_port,
    list_blocked_ports,
    remove_blocked_port,
)

pytestmark = pytest.mark.integration


async def create_admin(db: AsyncSession) -> User:
    actor = User(
        username="ddos-service-actor",
        role=Role.admin,
        password_hash="$argon2id$hash",
    )
    db.add(actor)
    await db.flush()
    return actor


async def test_hardcoded_amp_ports_constant() -> None:
    assert HARDCODED_AMP_PORTS == (17, 19, 53, 111, 123, 137, 161, 389, 520, 1900, 5353, 11211)


async def test_add_blocked_port_and_audit(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    entry = await add_blocked_port(db_session, actor, 9999, note="Test port block")

    assert entry.port == 9999
    assert entry.note == "Test port block"
    assert entry.created_by == actor.id

    ports = await list_blocked_ports(db_session)
    assert len(ports) == 1
    assert ports[0].port == 9999

    events = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "ddos.amp_port.added")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].target_id == "9999"
    assert events[0].target_type == "blocked_udp_port"
    assert events[0].metadata_ == {"note": "Test port block"}


async def test_add_duplicate_blocked_port_raises_409(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    await add_blocked_port(db_session, actor, 9999, note="Initial")

    with pytest.raises(HTTPException) as exc_info:
        await add_blocked_port(db_session, actor, 9999, note="Duplicate")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "port already blocked"


async def test_remove_blocked_port_and_audit(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    await add_blocked_port(db_session, actor, 8888, note="To remove")

    await remove_blocked_port(db_session, actor, 8888)

    ports = await list_blocked_ports(db_session)
    assert len(ports) == 0

    events = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "ddos.amp_port.removed")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].target_id == "8888"


async def test_remove_absent_blocked_port_raises_404(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await remove_blocked_port(db_session, actor, 7777)

    assert exc_info.value.status_code == 404


async def test_list_blocked_ports_ordering(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    await add_blocked_port(db_session, actor, 5000)
    await add_blocked_port(db_session, actor, 1000)
    await add_blocked_port(db_session, actor, 3000)

    ports = await list_blocked_ports(db_session)
    assert [p.port for p in ports] == [1000, 3000, 5000]
