import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, Role, Tenant, User
from app.services.audit import record_event

pytestmark = pytest.mark.integration


async def test_record_event_inserts_row_in_caller_transaction(
    db_session: AsyncSession,
) -> None:
    actor = User(username="audit-admin", role=Role.admin, password_hash="$argon2id$hash")
    tenant = Tenant(name="Audited Tenant")
    db_session.add_all([actor, tenant])
    await db_session.flush()

    await record_event(
        db_session,
        actor=actor,
        action="tenant.create",
        target_type="tenant",
        target_id=str(tenant.id),
        outcome="success",
        ip="127.0.0.1",
        metadata={"safe": "value", "password": "plain"},
    )
    await db_session.flush()

    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "tenant.create"))
    ).scalar_one()
    assert event.actor_user_id == actor.id
    assert event.actor_username == "audit-admin"
    assert event.target_id == str(tenant.id)
    assert event.metadata_ == {"safe": "value"}


async def test_record_event_does_not_commit_outside_caller_transaction(
    db_session: AsyncSession,
) -> None:
    actor = User(username="rollback-admin", role=Role.admin, password_hash="$argon2id$hash")
    target_id = str(uuid.uuid4())
    db_session.add(actor)
    await db_session.flush()

    await record_event(
        db_session,
        actor=actor,
        action="tenant.rollback",
        target_type="tenant",
        target_id=target_id,
        outcome="success",
        metadata=None,
    )
    await db_session.flush()
    await db_session.rollback()

    event = await db_session.execute(
        select(AuditEvent).where(AuditEvent.action == "tenant.rollback")
    )
    assert event.scalar_one_or_none() is None


async def test_actor_username_snapshot_survives_actor_delete(
    db_session: AsyncSession,
) -> None:
    actor = User(username="deleted-actor", role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(actor)
    await db_session.flush()

    await record_event(
        db_session,
        actor=actor,
        action="user.delete",
        target_type="user",
        target_id=str(uuid.uuid4()),
        outcome="success",
    )
    await db_session.flush()
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.delete"))
    ).scalar_one()

    await db_session.delete(actor)
    await db_session.flush()
    await db_session.refresh(event)

    assert event.actor_user_id is None
    assert event.actor_username == "deleted-actor"
