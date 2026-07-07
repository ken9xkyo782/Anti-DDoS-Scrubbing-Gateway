from ipaddress import IPv4Network
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Principal, load_service_for_principal
from app.db.models import Role, Tenant, User
from app.services import allocations as allocation_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "service-loader-admin") -> User:
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


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
    name: str,
    cidr: str,
) -> service_service.ServiceRecord:
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network(cidr),
        actor=actor,
    )
    service_ip = next(IPv4Network(cidr).hosts())
    return await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name=name,
        cidr_or_ip=IPv4Network(f"{service_ip}/32"),
        actor=actor,
    )


async def test_owner_and_admin_load_service(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Loader Owner Tenant")
    owner = await create_tenant_user(db_session, username="loader-owner", tenant=tenant)
    service = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="loader-edge",
        cidr="203.0.113.0/24",
    )

    owner_loaded = await load_service_for_principal(
        db_session,
        service.service.id,
        principal_for(owner),
    )
    admin_loaded = await load_service_for_principal(
        db_session,
        service.service.id,
        principal_for(admin),
    )

    assert owner_loaded.id == service.service.id
    assert admin_loaded.id == service.service.id


async def test_cross_tenant_load_is_zero_leak_404(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "loader-cross-admin")
    own_tenant = await create_tenant(db_session, "Loader Own Tenant")
    other_tenant = await create_tenant(db_session, "Loader Other Tenant")
    other_user = await create_tenant_user(db_session, username="loader-other", tenant=other_tenant)
    service = await create_service(
        db_session,
        tenant=own_tenant,
        actor=admin,
        name="secret-edge",
        cidr="203.0.113.0/24",
    )

    with pytest.raises(HTTPException) as exc_info:
        await load_service_for_principal(db_session, service.service.id, principal_for(other_user))

    assert exc_info.value.status_code == 404
    assert "secret-edge" not in str(exc_info.value.detail)
    assert str(own_tenant.id) not in str(exc_info.value.detail)


async def test_unknown_service_load_is_404(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "loader-unknown-admin")

    with pytest.raises(HTTPException) as exc_info:
        await load_service_for_principal(db_session, uuid4(), principal_for(admin))

    assert exc_info.value.status_code == 404
