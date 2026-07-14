import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, NodeControl, Role, User
from app.services.node_control import (
    get_node_control,
    maintenance_active,
    set_bypass,
    set_maintenance,
)

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession) -> User:
    actor = User(
        username="node-control-service-admin",
        role=Role.admin,
        password_hash="$argon2id$hash",
    )
    db_session.add(actor)
    await db_session.flush()
    return actor


async def audit_events(db_session: AsyncSession, action: str) -> list[AuditEvent]:
    return list(
        (
            await db_session.execute(
                select(AuditEvent)
                .where(AuditEvent.action == action)
                .order_by(AuditEvent.created_at)
            )
        )
        .scalars()
        .all()
    )


async def test_get_node_control_creates_one_disabled_singleton(db_session: AsyncSession) -> None:
    first = await get_node_control(db_session)
    second = await get_node_control(db_session)

    assert first.id == 1
    assert second.id == first.id
    assert first.bypass_enabled is False
    assert first.maintenance_enabled is False
    assert (await db_session.scalar(select(func.count(NodeControl.id)))) == 1


async def test_set_bypass_audits_state_changes_and_ignores_identical_state(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)

    control = await set_bypass(
        db_session,
        actor,
        True,
        "Emergency traffic mitigation",
        "198.51.100.10",
    )

    assert control.bypass_enabled is True
    assert control.bypass_reason == "Emergency traffic mitigation"
    assert control.bypass_activated_at is not None
    assert control.bypass_actor_user_id == actor.id
    enabled_audits = await audit_events(db_session, "node.bypass.enabled")
    assert len(enabled_audits) == 1
    assert enabled_audits[0].outcome == "success"
    assert enabled_audits[0].target_type == "node_control"
    assert enabled_audits[0].target_id == "1"
    assert enabled_audits[0].ip_address == "198.51.100.10"
    assert enabled_audits[0].metadata_ == {"reason": "Emergency traffic mitigation"}

    updated_at = control.updated_at
    same_control = await set_bypass(
        db_session,
        actor,
        True,
        "Ignored replacement reason",
        "198.51.100.11",
    )

    assert same_control.updated_at == updated_at
    assert same_control.bypass_reason == "Emergency traffic mitigation"
    assert len(await audit_events(db_session, "node.bypass.enabled")) == 1

    control = await set_bypass(
        db_session,
        actor,
        False,
        "Mitigation complete",
        "198.51.100.12",
    )

    assert control.bypass_enabled is False
    assert control.bypass_reason is None
    assert control.bypass_activated_at is None
    disabled_audits = await audit_events(db_session, "node.bypass.disabled")
    assert len(disabled_audits) == 1
    assert disabled_audits[0].outcome == "success"
    assert disabled_audits[0].target_type == "node_control"
    assert disabled_audits[0].target_id == "1"
    assert disabled_audits[0].ip_address == "198.51.100.12"
    assert disabled_audits[0].metadata_ == {"reason": "Mitigation complete"}


async def test_set_maintenance_audits_state_changes_and_worker_gate_reads_state(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)

    assert await maintenance_active(db_session) is False
    control = await set_maintenance(db_session, actor, True, "198.51.100.20")

    assert control.maintenance_enabled is True
    assert control.maintenance_activated_at is not None
    assert control.maintenance_actor_user_id == actor.id
    assert await maintenance_active(db_session) is True
    enabled_audits = await audit_events(db_session, "node.maintenance.enabled")
    assert len(enabled_audits) == 1
    assert enabled_audits[0].outcome == "success"
    assert enabled_audits[0].target_type == "node_control"
    assert enabled_audits[0].target_id == "1"
    assert enabled_audits[0].ip_address == "198.51.100.20"

    updated_at = control.updated_at
    same_control = await set_maintenance(db_session, actor, True, "198.51.100.21")

    assert same_control.updated_at == updated_at
    assert len(await audit_events(db_session, "node.maintenance.enabled")) == 1

    control = await set_maintenance(db_session, actor, False, "198.51.100.22")

    assert control.maintenance_enabled is False
    assert control.maintenance_activated_at is None
    assert await maintenance_active(db_session) is False
    disabled_audits = await audit_events(db_session, "node.maintenance.disabled")
    assert len(disabled_audits) == 1
    assert disabled_audits[0].outcome == "success"
    assert disabled_audits[0].target_type == "node_control"
    assert disabled_audits[0].target_id == "1"
    assert disabled_audits[0].ip_address == "198.51.100.22"
