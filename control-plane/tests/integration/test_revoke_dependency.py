from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, Tenant, User
from app.services import allocations as allocation_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "revoke-dependency-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(db_session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def test_revoke_allocation_with_service_returns_409_and_names_blocker(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Blocked Revoke Dependency Tenant")
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("203.0.113.0/24"),
        actor=admin,
    )
    await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name="edge-blocker",
        cidr_or_ip=IPv4Network("203.0.113.10/32"),
        actor=admin,
    )

    with pytest.raises(HTTPException) as exc_info:
        await allocation_service.revoke(db_session, allocation_id=allocation.id, actor=admin)

    assert exc_info.value.status_code == 409
    assert "edge-blocker" in str(exc_info.value.detail)


async def test_revoke_succeeds_after_service_is_deleted(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "revoke-after-delete-admin")
    tenant = await create_tenant(db_session, "Revoke After Delete Tenant")
    allocation = await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("198.51.100.0/24"),
        actor=admin,
    )
    service = await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name="edge-delete",
        cidr_or_ip=IPv4Network("198.51.100.10/32"),
        actor=admin,
    )
    await service_service.delete_service(db_session, service_id=service.service.id, actor=admin)

    revoked = await allocation_service.revoke(db_session, allocation_id=allocation.id, actor=admin)

    assert revoked.id == allocation.id
    assert revoked.status == "revoked"


async def test_main_import_has_no_service_allocation_cycle() -> None:
    import app.main

    assert app.main.app is not None
