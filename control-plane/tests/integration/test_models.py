import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, Role, Tenant, User, UserStatus


async def test_role_tenant_check_rejects_admin_with_tenant(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Tenant A")
    db_session.add(tenant)
    await db_session.flush()

    db_session.add(
        User(
            username="admin-with-tenant",
            role=Role.admin,
            tenant_id=tenant.id,
            password_hash="$argon2id$hash",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_role_tenant_check_rejects_tenant_user_without_tenant(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        User(
            username="tenant-without-tenant",
            role=Role.tenant_user,
            tenant_id=None,
            password_hash="$argon2id$hash",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_username_is_unique_case_insensitive(db_session: AsyncSession) -> None:
    db_session.add(
        User(
            username="Alice",
            role=Role.admin,
            password_hash="$argon2id$hash",
        )
    )
    await db_session.flush()

    db_session.add(
        User(
            username="alice",
            role=Role.admin,
            password_hash="$argon2id$hash",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_deleting_user_keeps_audit_snapshot_and_nulls_actor(
    db_session: AsyncSession,
) -> None:
    actor = User(
        username="auditor",
        role=Role.admin,
        password_hash="$argon2id$hash",
        status=UserStatus.active,
    )
    db_session.add(actor)
    await db_session.flush()

    event = AuditEvent(
        actor_user_id=actor.id,
        actor_username=actor.username,
        action="user.delete",
        target_type="user",
        target_id=str(uuid.uuid4()),
        outcome="success",
        metadata={"reason": "test"},
    )
    db_session.add(event)
    await db_session.flush()

    await db_session.delete(actor)
    await db_session.flush()
    await db_session.refresh(event)

    assert event.actor_user_id is None
    assert event.actor_username == "auditor"
