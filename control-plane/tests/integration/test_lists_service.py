from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditEvent,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    Role,
    Tenant,
    User,
    WhitelistEntry,
)
from app.services import allocations as allocation_service
from app.services import lists as list_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "list-admin") -> User:
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


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
) -> service_service.ServiceRecord:
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("203.0.113.0/24"),
        actor=actor,
    )
    return await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name="list-service",
        cidr_or_ip=IPv4Network("203.0.113.10/32"),
        actor=actor,
    )


async def test_add_whitelist_accepts_external_ipv4_bumps_and_audits(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Whitelist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="198.51.100.7/32",
        actor=admin,
    )

    assert str(entry.source_cidr) == "198.51.100.7/32"
    assert service.service.version == 2
    audit = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "list.whitelist.add")
        )
    ).scalar_one()
    assert audit.target_id == str(entry.id)


@pytest.mark.parametrize("source_cidr", ["2001:db8::/48", "198.51.100.7/24"])
async def test_add_whitelist_rejects_ipv6_and_host_bits(
    db_session: AsyncSession,
    source_cidr: str,
) -> None:
    admin = await create_admin(db_session, f"whitelist-invalid-{source_cidr}")
    tenant = await create_tenant(db_session, f"Whitelist Invalid {source_cidr}")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    with pytest.raises(HTTPException) as exc_info:
        await list_service.add_whitelist(
            db_session,
            service_id=service.service.id,
            source_cidr=source_cidr,
            actor=admin,
        )

    assert exc_info.value.status_code == 422


async def test_add_whitelist_does_not_require_source_inside_allocation(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "whitelist-external-admin")
    tenant = await create_tenant(db_session, "Whitelist External Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="45.0.0.0/8",
        actor=admin,
    )

    assert str(entry.source_cidr) == "45.0.0.0/8"


async def test_add_service_blacklist_bumps_and_audits(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "service-blacklist-admin")
    tenant = await create_tenant(db_session, "Service Blacklist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_blacklist(
        db_session,
        scope=BlacklistScope.service,
        service_id=service.service.id,
        source_cidr="45.0.0.0/8",
        actor=admin,
    )

    assert entry.scope == BlacklistScope.service
    assert service.service.version == 2
    audit = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "list.blacklist.add")
        )
    ).scalar_one()
    assert audit.target_id == str(entry.id)


async def test_add_service_blacklist_rejects_ipv6(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "service-blacklist-invalid-admin")
    tenant = await create_tenant(db_session, "Service Blacklist Invalid Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    with pytest.raises(HTTPException) as exc_info:
        await list_service.add_blacklist(
            db_session,
            scope=BlacklistScope.service,
            service_id=service.service.id,
            source_cidr="2001:db8::/48",
            actor=admin,
        )

    assert exc_info.value.status_code == 422


async def test_same_source_can_exist_in_whitelist_and_blacklist(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "list-coexist-admin")
    tenant = await create_tenant(db_session, "List Coexist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    whitelist = await list_service.add_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="198.51.100.7/32",
        actor=admin,
    )
    blacklist = await list_service.add_blacklist(
        db_session,
        scope=BlacklistScope.service,
        service_id=service.service.id,
        source_cidr="198.51.100.7/32",
        actor=admin,
    )

    assert whitelist.source_cidr == blacklist.source_cidr


async def test_add_global_blacklist_has_manual_source_and_no_version_bump(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "global-blacklist-admin")
    tenant = await create_tenant(db_session, "Global Blacklist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_blacklist(
        db_session,
        scope=BlacklistScope.global_,
        service_id=None,
        source_cidr="185.0.0.0/8",
        actor=admin,
    )

    assert entry.service_id is None
    assert entry.source == BlacklistSource.manual
    assert service.service.version == 1


async def test_list_and_remove_service_lists_are_scoped(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "list-remove-admin")
    tenant = await create_tenant(db_session, "List Remove Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    await list_service.add_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="198.51.100.7/32",
        actor=admin,
    )
    await list_service.add_blacklist(
        db_session,
        scope=BlacklistScope.service,
        service_id=service.service.id,
        source_cidr="45.0.0.0/8",
        actor=admin,
    )

    whitelist = await list_service.list_whitelist(
        db_session,
        service_id=service.service.id,
        actor=admin,
    )
    blacklist = await list_service.list_blacklist(
        db_session,
        scope=BlacklistScope.service,
        service_id=service.service.id,
        actor=admin,
    )
    await list_service.remove_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="198.51.100.7/32",
        actor=admin,
    )
    await list_service.remove_blacklist(
        db_session,
        scope=BlacklistScope.service,
        service_id=service.service.id,
        source_cidr="45.0.0.0/8",
        actor=admin,
    )

    assert [str(entry.source_cidr) for entry in whitelist] == ["198.51.100.7/32"]
    assert [str(entry.source_cidr) for entry in blacklist] == ["45.0.0.0/8"]
    assert (await db_session.execute(select(func.count(WhitelistEntry.id)))).scalar_one() == 0
    assert (await db_session.execute(select(func.count(BlacklistEntry.id)))).scalar_one() == 0


async def test_global_blacklist_list_remove_requires_admin(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "global-list-admin")
    tenant = await create_tenant(db_session, "Global List Tenant")
    tenant_user = await create_tenant_user(db_session, username="global-list-user", tenant=tenant)
    entry = await list_service.add_blacklist(
        db_session,
        scope=BlacklistScope.global_,
        service_id=None,
        source_cidr="185.0.0.0/8",
        actor=admin,
    )

    listed = await list_service.list_blacklist(
        db_session,
        scope=BlacklistScope.global_,
        service_id=None,
        actor=admin,
    )
    await list_service.remove_blacklist(
        db_session,
        scope=BlacklistScope.global_,
        service_id=None,
        source_cidr="185.0.0.0/8",
        actor=admin,
    )
    with pytest.raises(HTTPException) as exc_info:
        await list_service.list_blacklist(
            db_session,
            scope=BlacklistScope.global_,
            service_id=None,
            actor=tenant_user,
        )

    assert [row.id for row in listed] == [entry.id]
    assert exc_info.value.status_code == 403
