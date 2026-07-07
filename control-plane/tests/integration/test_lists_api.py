from collections.abc import AsyncGenerator
from ipaddress import IPv4Network

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import lists
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import Role, Tenant, User
from app.db.session import get_db
from app.services import allocations as allocation_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(lists.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "lists-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
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
        password_hash=hash_password("tenant-pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


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
        name="lists-api-service",
        cidr_or_ip=IPv4Network("203.0.113.10/32"),
        actor=actor,
    )


async def test_add_whitelist_arbitrary_ipv4_returns_201(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Lists API Whitelist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/whitelist",
            json={"source_cidr": "198.51.100.7/32"},
        )

    assert response.status_code == 201
    assert response.json()["source_cidr"] == "198.51.100.7/32"


async def test_add_whitelist_ipv6_returns_422(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "lists-api-whitelist-invalid-admin")
    tenant = await create_tenant(db_session, "Lists API Whitelist Invalid Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/whitelist",
            json={"source_cidr": "2001:db8::/48"},
        )

    assert response.status_code == 422


async def test_add_service_blacklist_returns_201(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "lists-api-blacklist-admin")
    tenant = await create_tenant(db_session, "Lists API Blacklist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/blacklist",
            json={"source_cidr": "45.0.0.0/8"},
        )

    assert response.status_code == 201
    assert response.json()["scope"] == "service"


async def test_add_service_blacklist_ipv6_returns_422(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "lists-api-blacklist-invalid-admin")
    tenant = await create_tenant(db_session, "Lists API Blacklist Invalid Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/blacklist",
            json={"source_cidr": "2001:db8::/48"},
        )

    assert response.status_code == 422


async def test_same_source_can_be_whitelisted_and_blacklisted(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "lists-api-coexist-admin")
    tenant = await create_tenant(db_session, "Lists API Coexist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        whitelist = await client.post(
            f"/services/{service.service.id}/whitelist",
            json={"source_cidr": "198.51.100.7/32"},
        )
        blacklist = await client.post(
            f"/services/{service.service.id}/blacklist",
            json={"source_cidr": "198.51.100.7/32"},
        )

    assert whitelist.status_code == 201
    assert blacklist.status_code == 201


async def test_list_and_delete_service_lists(db_session: AsyncSession, redis_client: Redis) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "lists-api-list-delete-admin")
    tenant = await create_tenant(db_session, "Lists API List Delete Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await client.post(
            f"/services/{service.service.id}/whitelist",
            json={"source_cidr": "198.51.100.7/32"},
        )
        await client.post(
            f"/services/{service.service.id}/blacklist",
            json={"source_cidr": "45.0.0.0/8"},
        )
        whitelist = await client.get(f"/services/{service.service.id}/whitelist")
        blacklist = await client.get(f"/services/{service.service.id}/blacklist")
        whitelist_delete = await client.delete(
            f"/services/{service.service.id}/whitelist",
            params={"source_cidr": "198.51.100.7/32"},
        )
        blacklist_delete = await client.delete(
            f"/services/{service.service.id}/blacklist",
            params={"source_cidr": "45.0.0.0/8"},
        )

    assert [row["source_cidr"] for row in whitelist.json()] == ["198.51.100.7/32"]
    assert [row["source_cidr"] for row in blacklist.json()] == ["45.0.0.0/8"]
    assert whitelist_delete.status_code == 204
    assert blacklist_delete.status_code == 204


async def test_cross_tenant_list_access_returns_zero_leak_404(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "lists-api-cross-admin")
    own_tenant = await create_tenant(db_session, "Lists API Cross Own Tenant")
    other_tenant = await create_tenant(db_session, "Lists API Cross Other Tenant")
    other_user = await create_tenant_user(
        db_session, username="lists-api-cross-user", tenant=other_tenant
    )
    service = await create_service(db_session, tenant=own_tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, other_user)
        response = await client.get(f"/services/{service.service.id}/whitelist")

    assert response.status_code == 404
    assert "lists-api-service" not in response.text
    assert str(own_tenant.id) not in response.text
