from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, CIDRStatus, Role, Tenant, TenantStatus, User
from app.services import allocations as allocation_service
from app.services import tenants as tenant_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "tenant-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def test_create_tenant_persists_active_and_audits(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)

    tenant = await tenant_service.create_tenant(db_session, actor=actor, name="Acme")

    assert tenant.status == TenantStatus.active
    audit = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "tenant.create"))
    ).scalar_one()
    assert audit.target_id == str(tenant.id)
    assert audit.outcome == "success"


async def test_create_tenant_rejects_case_insensitive_duplicate(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    await tenant_service.create_tenant(db_session, actor=actor, name="Duplicate")

    with pytest.raises(HTTPException) as exc_info:
        await tenant_service.create_tenant(db_session, actor=actor, name="duplicate")

    assert exc_info.value.status_code == 409


async def test_update_tenant_and_status_changes_audit(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await tenant_service.create_tenant(db_session, actor=actor, name="Mutable")

    updated = await tenant_service.update_tenant(
        db_session,
        actor=actor,
        tenant_id=tenant.id,
        name="Mutable Renamed",
    )
    suspended = await tenant_service.set_status(
        db_session,
        actor=actor,
        tenant_id=tenant.id,
        status=TenantStatus.suspended,
    )
    assert suspended.status == TenantStatus.suspended
    reactivated = await tenant_service.set_status(
        db_session,
        actor=actor,
        tenant_id=tenant.id,
        status=TenantStatus.active,
    )

    assert updated.name == "Mutable Renamed"
    assert reactivated.status == TenantStatus.active
    actions = (
        (
            await db_session.execute(
                select(AuditEvent.action).where(
                    AuditEvent.action.in_(["tenant.update", "tenant.status"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert actions.count("tenant.update") == 1
    assert actions.count("tenant.status") == 2


async def test_list_tenants_returns_allocation_and_user_counts(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await tenant_service.create_tenant(db_session, actor=actor, name="Counted")
    user = User(
        username="counted-user",
        role=Role.tenant_user,
        tenant=tenant,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.80.0.0/24"),
        actor=actor,
    )
    revoked = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.81.0.0/24"),
        actor=actor,
    )
    await allocation_service.revoke(db_session, allocation_id=revoked.id, actor=actor)

    rows = await tenant_service.list_tenants(db_session)
    counted = next(row for row in rows if row.tenant.id == tenant.id)

    assert counted.user_count == 1
    assert counted.active_allocation_count == 1
    assert allocation.status == CIDRStatus.active


async def test_delete_tenant_blocked_while_user_exists_and_audits_denial(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await tenant_service.create_tenant(db_session, actor=actor, name="User Blocked")
    user = User(
        username="blocking-user",
        role=Role.tenant_user,
        tenant=tenant,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await tenant_service.delete_tenant(db_session, actor=actor, tenant_id=tenant.id)

    assert exc_info.value.status_code == 409
    assert "users" in str(exc_info.value.detail)
    audit = (
        await db_session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == "tenant.delete")
            .where(AuditEvent.outcome == "denied")
        )
    ).scalar_one()
    assert audit.target_id == str(tenant.id)


async def test_delete_tenant_blocked_while_non_revoked_cidr_exists(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await tenant_service.create_tenant(db_session, actor=actor, name="CIDR Blocked")
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.90.0.0/24"),
        actor=actor,
    )

    with pytest.raises(HTTPException) as exc_info:
        await tenant_service.delete_tenant(db_session, actor=actor, tenant_id=tenant.id)

    assert exc_info.value.status_code == 409
    assert "allocations" in str(exc_info.value.detail)


async def test_delete_empty_tenant_hard_deletes_and_audits_success(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await tenant_service.create_tenant(db_session, actor=actor, name="Empty Delete")

    await tenant_service.delete_tenant(db_session, actor=actor, tenant_id=tenant.id)

    assert await db_session.get(Tenant, tenant.id) is None
    audit = (
        await db_session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == "tenant.delete")
            .where(AuditEvent.outcome == "success")
        )
    ).scalar_one()
    assert audit.target_id == str(tenant.id)
