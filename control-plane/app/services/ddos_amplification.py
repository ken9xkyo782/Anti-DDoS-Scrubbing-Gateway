from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BlockedUdpPort, User
from app.services.audit import record_event

HARDCODED_AMP_PORTS: tuple[int, ...] = (
    17,
    19,
    53,
    111,
    123,
    137,
    161,
    389,
    520,
    1900,
    5353,
    11211,
)
"""mirror of data-plane amp_port_hardcoded (blacklist.h)
— DP header authoritative; change both together
"""


async def list_blocked_ports(db: AsyncSession) -> list[BlockedUdpPort]:
    result = await db.execute(select(BlockedUdpPort).order_by(BlockedUdpPort.port.asc()))
    return list(result.scalars().all())


async def add_blocked_port(
    db: AsyncSession,
    actor: User | None,
    port: int,
    note: str | None = None,
    ip: str | None = None,
) -> BlockedUdpPort:
    existing = await db.get(BlockedUdpPort, port)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="port already blocked",
        )

    entry = BlockedUdpPort(
        port=port,
        note=note,
        created_by=actor.id if actor is not None else None,
    )
    db.add(entry)
    await db.flush()

    await record_event(
        db,
        actor=actor,
        action="ddos.amp_port.added",
        target_type="blocked_udp_port",
        target_id=str(port),
        outcome="success",
        ip=ip,
        metadata={"note": note},
    )
    return entry


async def remove_blocked_port(
    db: AsyncSession,
    actor: User | None,
    port: int,
    ip: str | None = None,
) -> None:
    entry = await db.get(BlockedUdpPort, port)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="blocked port not found",
        )

    await db.delete(entry)
    await db.flush()

    await record_event(
        db,
        actor=actor,
        action="ddos.amp_port.removed",
        target_type="blocked_udp_port",
        target_id=str(port),
        outcome="success",
        ip=ip,
    )
