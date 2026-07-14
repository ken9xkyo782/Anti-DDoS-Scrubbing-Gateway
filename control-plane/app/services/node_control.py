from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NodeControl, User, utc_now
from app.services.audit import record_event


async def get_node_control(db: AsyncSession) -> NodeControl:
    await _ensure_node_control(db)
    return (await db.execute(select(NodeControl).where(NodeControl.id == 1))).scalars().one()


async def set_bypass(
    db: AsyncSession,
    actor: User | None,
    enabled: bool,
    reason: str | None,
    ip: str | None,
) -> NodeControl:
    control = await _locked_node_control(db)
    if control.bypass_enabled == enabled:
        return control

    now = utc_now()
    control.bypass_enabled = enabled
    control.bypass_reason = reason if enabled else None
    control.bypass_activated_at = now if enabled else None
    control.bypass_actor_user_id = actor.id if actor is not None else None
    control.updated_at = now
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="node.bypass.enabled" if enabled else "node.bypass.disabled",
        target_type="node_control",
        target_id=str(control.id),
        outcome="success",
        ip=ip,
        metadata={"reason": reason},
    )
    return control


async def set_maintenance(
    db: AsyncSession,
    actor: User | None,
    enabled: bool,
    ip: str | None,
) -> NodeControl:
    control = await _locked_node_control(db)
    if control.maintenance_enabled == enabled:
        return control

    now = utc_now()
    control.maintenance_enabled = enabled
    control.maintenance_activated_at = now if enabled else None
    control.maintenance_actor_user_id = actor.id if actor is not None else None
    control.updated_at = now
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="node.maintenance.enabled" if enabled else "node.maintenance.disabled",
        target_type="node_control",
        target_id=str(control.id),
        outcome="success",
        ip=ip,
    )
    return control


async def maintenance_active(db: AsyncSession) -> bool:
    return (await get_node_control(db)).maintenance_enabled


async def _locked_node_control(db: AsyncSession) -> NodeControl:
    await _ensure_node_control(db)
    return (
        (await db.execute(select(NodeControl).where(NodeControl.id == 1).with_for_update()))
        .scalars()
        .one()
    )


async def _ensure_node_control(db: AsyncSession) -> None:
    now = utc_now()
    await db.execute(
        pg_insert(NodeControl)
        .values(
            id=1,
            bypass_enabled=False,
            maintenance_enabled=False,
            bypass_reason=None,
            bypass_activated_at=None,
            maintenance_activated_at=None,
            bypass_actor_user_id=None,
            maintenance_actor_user_id=None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[NodeControl.id])
    )
