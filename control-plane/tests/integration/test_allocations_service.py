from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AllocatedCIDR, AuditEvent, CIDRStatus, Role, Tenant, TenantStatus, User
from app.services import allocations as allocation_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "allocation-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(
    db_session: AsyncSession,
    *,
    name: str,
    status: TenantStatus = TenantStatus.active,
) -> Tenant:
    tenant = Tenant(name=name, status=status)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def test_allocate_to_active_tenant_persists_and_audits(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Allocation Tenant")

    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("203.0.113.0/24"),
        actor=actor,
    )

    assert allocation.status == CIDRStatus.active
    assert allocation.allocated_by == actor.id
    assert str(allocation.cidr) == "203.0.113.0/24"
    audit = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "allocation.allocate")
        )
    ).scalar_one()
    assert audit.target_id == str(allocation.id)
    assert audit.outcome == "success"


async def test_allocate_overlap_returns_409_with_conflicting_range(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Conflict Tenant")
    other = await create_tenant(db_session, name="Conflict Other")
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("198.51.100.0/24"),
        actor=actor,
    )

    with pytest.raises(HTTPException) as exc_info:
        await allocation_service.allocate(
            db_session,
            tenant_id=other.id,
            cidr=IPv4Network("198.51.100.128/25"),
            actor=actor,
        )

    assert exc_info.value.status_code == 409
    assert "198.51.100.0/24" in str(exc_info.value.detail)


async def test_allocate_to_suspended_or_missing_tenant_is_refused(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    suspended = await create_tenant(
        db_session,
        name="Suspended Allocation Tenant",
        status=TenantStatus.suspended,
    )

    with pytest.raises(HTTPException) as suspended_exc:
        await allocation_service.allocate(
            db_session,
            tenant_id=suspended.id,
            cidr=IPv4Network("192.0.2.0/24"),
            actor=actor,
        )
    with pytest.raises(HTTPException) as missing_exc:
        await allocation_service.allocate(
            db_session,
            tenant_id=actor.id,
            cidr=IPv4Network("192.0.3.0/24"),
            actor=actor,
        )

    assert suspended_exc.value.status_code == 409
    assert missing_exc.value.status_code == 404


async def test_revoke_empty_allocation_audits_and_is_idempotent(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Revoke Tenant")
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("172.20.0.0/24"),
        actor=actor,
    )

    revoked = await allocation_service.revoke(db_session, allocation_id=allocation.id, actor=actor)
    revoked_again = await allocation_service.revoke(
        db_session,
        allocation_id=allocation.id,
        actor=actor,
    )

    assert revoked.status == CIDRStatus.revoked
    assert revoked_again.id == allocation.id
    revoke_audits = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "allocation.revoke"))
    ).scalars()
    assert [event.outcome for event in revoke_audits] == ["success"]


async def test_revoke_frees_range_for_reallocation(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Reallocate Tenant")
    other = await create_tenant(db_session, name="Reallocate Other")
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("172.22.0.0/24"),
        actor=actor,
    )
    await allocation_service.revoke(db_session, allocation_id=allocation.id, actor=actor)

    replacement = await allocation_service.allocate(
        db_session,
        tenant_id=other.id,
        cidr=IPv4Network("172.22.0.0/24"),
        actor=actor,
    )

    assert replacement.status == CIDRStatus.active
    assert replacement.tenant_id == other.id


async def test_revoke_with_dependents_returns_409_and_audits_denial(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Blocked Revoke Tenant")
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("172.21.0.0/24"),
        actor=actor,
    )

    async def fake_dependents(
        _db: AsyncSession,
        _allocation: AllocatedCIDR,
    ) -> list[str]:
        return ["protected_service:svc-1"]

    monkeypatch.setattr(allocation_service, "count_allocation_dependents", fake_dependents)

    with pytest.raises(HTTPException) as exc_info:
        await allocation_service.revoke(db_session, allocation_id=allocation.id, actor=actor)

    assert exc_info.value.status_code == 409
    assert "protected_service:svc-1" in str(exc_info.value.detail)
    await db_session.refresh(allocation)
    assert allocation.status == CIDRStatus.active
    audit = (
        await db_session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == "allocation.revoke")
            .where(AuditEvent.outcome == "denied")
        )
    ).scalar_one()
    assert audit.target_id == str(allocation.id)


async def test_overlap_check_reports_conflicts_without_writing(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Overlap Check Tenant")
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.40.0.0/24"),
        actor=actor,
    )
    before = (await db_session.execute(select(func.count(AllocatedCIDR.id)))).scalar_one()

    result = await allocation_service.overlap_check(db_session, IPv4Network("10.40.0.128/25"))
    clear = await allocation_service.overlap_check(db_session, IPv4Network("10.41.0.0/24"))
    after = (await db_session.execute(select(func.count(AllocatedCIDR.id)))).scalar_one()

    assert result.overlaps
    assert [str(conflict.cidr) for conflict in result.conflicts] == ["10.40.0.0/24"]
    assert not clear.overlaps
    assert clear.conflicts == []
    assert after == before


async def test_list_for_tenant_returns_usage_summary(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="List Allocation Tenant")
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.50.0.0/24"),
        actor=actor,
    )

    rows = await allocation_service.list_for_tenant(db_session, tenant.id)

    assert len(rows) == 1
    assert rows[0].allocation.id == allocation.id
    assert rows[0].dependent_count == 0


async def test_cidr_in_tenant_allocation_truth_table(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Scope Tenant")
    revoked_tenant = await create_tenant(db_session, name="Revoked Scope Tenant")
    active = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.60.0.0/24"),
        actor=actor,
    )
    revoked = await allocation_service.allocate(
        db_session,
        tenant_id=revoked_tenant.id,
        cidr=IPv4Network("10.61.0.0/24"),
        actor=actor,
    )
    await allocation_service.revoke(db_session, allocation_id=revoked.id, actor=actor)

    assert await allocation_service.cidr_in_tenant_allocation(
        db_session,
        active.tenant_id,
        IPv4Network("10.60.0.10/32"),
    )
    assert not await allocation_service.cidr_in_tenant_allocation(
        db_session,
        active.tenant_id,
        IPv4Network("10.60.0.0/23"),
    )
    assert not await allocation_service.cidr_in_tenant_allocation(
        db_session,
        revoked_tenant.id,
        IPv4Network("10.61.0.10/32"),
    )
    assert not await allocation_service.cidr_in_tenant_allocation(
        db_session,
        actor.id,
        IPv4Network("10.60.0.10/32"),
    )
