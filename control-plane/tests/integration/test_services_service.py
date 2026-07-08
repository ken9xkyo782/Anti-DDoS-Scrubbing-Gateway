from collections.abc import Sequence
from decimal import Decimal
from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal
from app.db.models import (
    AllowRule,
    ApplyStatus,
    AuditEvent,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    Role,
    ServicePlan,
    Tenant,
    User,
    WhitelistEntry,
)
from app.services import allocations as allocation_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "service-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(db_session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def create_tenant_user(
    db_session: AsyncSession,
    *,
    username: str,
    tenant: Tenant,
) -> User:
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


def principal_for(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        session_id="test-session",
    )


async def allocate(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
    cidr: str,
) -> None:
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network(cidr),
        actor=actor,
    )


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
    name: str = "edge",
    cidr: str = "203.0.113.10/32",
    committed: Decimal | None = None,
    ceiling: Decimal | None = None,
) -> service_service.ServiceRecord:
    return await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name=name,
        cidr_or_ip=IPv4Network(cidr),
        actor=actor,
        committed_clean_gbps=committed,
        ceiling_clean_gbps=ceiling,
    )


async def audit_actions(db_session: AsyncSession, action: str) -> Sequence[AuditEvent]:
    return (
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


async def test_create_service_inside_allocation_persists_plan_and_audits(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Create Service Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    record = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        committed=Decimal("2"),
        ceiling=Decimal("5"),
    )

    assert record.service.enabled is False
    assert record.service.apply_status == ApplyStatus.queued
    assert record.service.version == 1
    assert record.plan.committed_clean_gbps == Decimal("2")
    assert record.plan.ceiling_clean_gbps == Decimal("5")
    assert (await audit_actions(db_session, "service.create"))[0].target_id == str(
        record.service.id
    )


async def test_create_service_outside_allocation_returns_403(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "outside-service-admin")
    tenant = await create_tenant(db_session, "Outside Service Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    with pytest.raises(HTTPException) as exc_info:
        await create_service(db_session, tenant=tenant, actor=admin, cidr="198.51.100.10/32")

    assert exc_info.value.status_code == 403


async def test_tenant_user_plan_sizing_is_forbidden(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "tenant-size-admin")
    tenant = await create_tenant(db_session, "Tenant Size Tenant")
    tenant_user = await create_tenant_user(db_session, username="tenant-size-user", tenant=tenant)
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    with pytest.raises(HTTPException) as exc_info:
        await create_service(
            db_session,
            tenant=tenant,
            actor=tenant_user,
            committed=Decimal("1"),
            ceiling=Decimal("2"),
        )

    assert exc_info.value.status_code == 403


async def test_tenant_user_create_gets_default_plan(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "tenant-default-admin")
    tenant = await create_tenant(db_session, "Tenant Default Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="tenant-default-user", tenant=tenant
    )
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    record = await create_service(db_session, tenant=tenant, actor=tenant_user)

    assert record.plan.committed_clean_gbps == Decimal("0")
    assert record.plan.ceiling_clean_gbps == Decimal("0")


async def test_create_service_overlap_returns_409(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "overlap-service-admin")
    tenant = await create_tenant(db_session, "Overlap Service Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    await create_service(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/25")

    with pytest.raises(HTTPException) as exc_info:
        await create_service(
            db_session,
            tenant=tenant,
            actor=admin,
            name="nested",
            cidr="203.0.113.64/26",
        )

    assert exc_info.value.status_code == 409
    assert "edge" in str(exc_info.value.detail)


async def test_create_service_committed_over_ceiling_returns_422(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "plan-invalid-admin")
    tenant = await create_tenant(db_session, "Plan Invalid Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    with pytest.raises(HTTPException) as exc_info:
        await create_service(
            db_session,
            tenant=tenant,
            actor=admin,
            committed=Decimal("5"),
            ceiling=Decimal("2"),
        )

    assert exc_info.value.status_code == 422


async def test_update_service_bumps_version_pending_and_vip(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "update-service-admin")
    tenant = await create_tenant(db_session, "Update Service Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    record = await create_service(db_session, tenant=tenant, actor=admin)

    updated = await service_service.update_service(
        db_session,
        service_id=record.service.id,
        actor=admin,
        name="edge-renamed",
        vip_pps=1000,
        vip_bps=2000,
    )

    assert updated.service.name == "edge-renamed"
    assert updated.service.vip_pps == 1000
    assert updated.service.vip_bps == 2000
    assert updated.service.version == 2
    assert updated.service.apply_status == ApplyStatus.queued
    assert (await audit_actions(db_session, "service.update"))[0].target_id == str(
        record.service.id
    )


async def test_update_service_destination_overlap_returns_409(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "update-overlap-admin")
    tenant = await create_tenant(db_session, "Update Overlap Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="first",
        cidr="203.0.113.10/32",
    )
    second = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="second",
        cidr="203.0.113.20/32",
    )

    with pytest.raises(HTTPException) as exc_info:
        await service_service.update_service(
            db_session,
            service_id=second.service.id,
            actor=admin,
            cidr_or_ip=IPv4Network("203.0.113.10/32"),
        )

    assert exc_info.value.status_code == 409
    assert "first" in str(exc_info.value.detail)


async def test_enable_disable_audits_changes_and_noops(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "toggle-service-admin")
    tenant = await create_tenant(db_session, "Toggle Service Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    record = await create_service(db_session, tenant=tenant, actor=admin)

    enabled = await service_service.set_enabled(
        db_session,
        service_id=record.service.id,
        enabled=True,
        actor=admin,
    )
    enabled_version = enabled.version
    disabled = await service_service.set_enabled(
        db_session,
        service_id=record.service.id,
        enabled=False,
        actor=admin,
    )
    disabled_again = await service_service.set_enabled(
        db_session,
        service_id=record.service.id,
        enabled=False,
        actor=admin,
    )

    assert enabled_version == 2
    assert disabled.version == 3
    assert disabled_again.version == 3
    assert len(await audit_actions(db_session, "service.enable")) == 1
    disable_audits = await audit_actions(db_session, "service.disable")
    assert len(disable_audits) == 1
    assert disable_audits[0].metadata_["dangerous"] is True


async def test_disable_retains_list_children(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "disable-retain-admin")
    tenant = await create_tenant(db_session, "Disable Retain Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    record = await create_service(db_session, tenant=tenant, actor=admin)
    db_session.add_all(
        [
            WhitelistEntry(
                service_id=record.service.id,
                source_cidr="198.51.100.7/32",
                created_by=admin.id,
            ),
            BlacklistEntry(
                service_id=record.service.id,
                scope=BlacklistScope.service,
                source=BlacklistSource.manual,
                source_cidr="45.0.0.0/8",
                created_by=admin.id,
            ),
        ]
    )
    await db_session.flush()
    await service_service.set_enabled(
        db_session,
        service_id=record.service.id,
        enabled=False,
        actor=admin,
    )

    assert (await db_session.execute(select(func.count(WhitelistEntry.id)))).scalar_one() == 1
    assert (await db_session.execute(select(func.count(BlacklistEntry.id)))).scalar_one() == 1


async def test_size_plan_accepts_boundaries_and_warns_on_oversubscription(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "size-plan-admin")
    tenant = await create_tenant(db_session, "Size Plan Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    first = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="first",
        cidr="203.0.113.10/32",
        committed=Decimal("39"),
        ceiling=Decimal("39"),
    )
    second = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="second",
        cidr="203.0.113.20/32",
    )
    await service_service.set_enabled(
        db_session, service_id=first.service.id, enabled=True, actor=admin
    )
    await service_service.set_enabled(
        db_session, service_id=second.service.id, enabled=True, actor=admin
    )

    zero = await service_service.size_plan(
        db_session,
        service_id=second.service.id,
        actor=admin,
        committed_clean_gbps=Decimal("0"),
        ceiling_clean_gbps=Decimal("0"),
    )
    zero_committed = zero.plan.committed_clean_gbps
    warning = await service_service.size_plan(
        db_session,
        service_id=second.service.id,
        actor=admin,
        committed_clean_gbps=Decimal("5"),
        ceiling_clean_gbps=Decimal("5"),
    )

    assert zero_committed == Decimal("0")
    assert warning.warnings == ["Committed clean bandwidth 44.00 exceeds node capacity 40.00"]


async def test_size_plan_tenant_user_is_forbidden(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "size-forbidden-admin")
    tenant = await create_tenant(db_session, "Size Forbidden Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="size-forbidden-user", tenant=tenant
    )
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    record = await create_service(db_session, tenant=tenant, actor=admin)

    with pytest.raises(HTTPException) as exc_info:
        await service_service.size_plan(
            db_session,
            service_id=record.service.id,
            actor=tenant_user,
            committed_clean_gbps=Decimal("1"),
            ceiling_clean_gbps=Decimal("1"),
        )

    assert exc_info.value.status_code == 403


async def test_delete_enabled_refuses_then_disabled_deletes_children(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "delete-service-admin")
    tenant = await create_tenant(db_session, "Delete Service Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    record = await create_service(db_session, tenant=tenant, actor=admin)
    await service_service.set_enabled(
        db_session, service_id=record.service.id, enabled=True, actor=admin
    )
    db_session.add(AllowRule(service_id=record.service.id, priority=10, protocol="tcp"))
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await service_service.delete_service(db_session, service_id=record.service.id, actor=admin)
    await service_service.set_enabled(
        db_session,
        service_id=record.service.id,
        enabled=False,
        actor=admin,
    )
    await service_service.delete_service(db_session, service_id=record.service.id, actor=admin)

    assert exc_info.value.status_code == 409
    assert await db_session.get(ServicePlan, record.plan.id) is None
    assert (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one() == 0
    delete_audits = await audit_actions(db_session, "service.delete")
    assert [event.outcome for event in delete_audits] == ["denied", "success"]


async def test_services_in_cidr_returns_contained_services(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "dependency-query-admin")
    tenant = await create_tenant(db_session, "Dependency Query Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")
    record = await create_service(db_session, tenant=tenant, actor=admin, cidr="203.0.113.10/32")

    contained = await service_service.services_in_cidr(db_session, IPv4Network("203.0.113.0/24"))
    empty = await service_service.services_in_cidr(db_session, IPv4Network("198.51.100.0/24"))

    assert [service.id for service in contained] == [record.service.id]
    assert empty == []


async def test_list_and_get_services_scope_admin_and_tenant_user(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "scope-service-admin")
    own_tenant = await create_tenant(db_session, "Scope Own Tenant")
    other_tenant = await create_tenant(db_session, "Scope Other Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="scope-service-user", tenant=own_tenant
    )
    await allocate(db_session, tenant=own_tenant, actor=admin, cidr="203.0.113.0/24")
    await allocate(db_session, tenant=other_tenant, actor=admin, cidr="198.51.100.0/24")
    own = await create_service(
        db_session,
        tenant=own_tenant,
        actor=admin,
        name="own",
        cidr="203.0.113.10/32",
    )
    other = await create_service(
        db_session,
        tenant=other_tenant,
        actor=admin,
        name="other",
        cidr="198.51.100.10/32",
    )

    admin_rows = await service_service.list_services(db_session, principal_for(admin))
    tenant_rows = await service_service.list_services(db_session, principal_for(tenant_user))
    own_loaded = await service_service.get_service(
        db_session,
        service_id=own.service.id,
        principal=principal_for(tenant_user),
    )
    with pytest.raises(HTTPException) as exc_info:
        await service_service.get_service(
            db_session,
            service_id=other.service.id,
            principal=principal_for(tenant_user),
        )

    assert {row.service.id for row in admin_rows} == {own.service.id, other.service.id}
    assert [row.service.id for row in tenant_rows] == [own.service.id]
    assert own_loaded.service.id == own.service.id
    assert exc_info.value.status_code == 404
