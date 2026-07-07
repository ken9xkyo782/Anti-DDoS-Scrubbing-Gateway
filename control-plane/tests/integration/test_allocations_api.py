from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import allocations
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import Role, Tenant, TenantStatus, User
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(allocations.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "allocation-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
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


async def test_admin_allocate_and_list_allocations(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Allocation API Tenant")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await client.post(
            "/allocations",
            json={"tenant_id": str(tenant.id), "cidr": "203.0.113.0/24"},
        )
        listed = await client.get(f"/allocations?tenant_id={tenant.id}")

    assert created.status_code == 201
    assert created.json()["cidr"] == "203.0.113.0/24"
    assert listed.status_code == 200
    assert listed.json()[0]["allocation"]["id"] == created.json()["id"]


async def test_admin_allocate_overlap_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Overlap API Tenant")
    other = await create_tenant(db_session, name="Overlap API Other")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        first = await client.post(
            "/allocations",
            json={"tenant_id": str(tenant.id), "cidr": "198.51.100.0/24"},
        )
        conflict = await client.post(
            "/allocations",
            json={"tenant_id": str(other.id), "cidr": "198.51.100.128/25"},
        )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "198.51.100.0/24" in conflict.text


async def test_invalid_cidr_inputs_return_422(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Invalid CIDR API Tenant")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        responses = [
            await client.post(
                "/allocations",
                json={"tenant_id": str(tenant.id), "cidr": "2001:db8::/32"},
            ),
            await client.post(
                "/allocations",
                json={"tenant_id": str(tenant.id), "cidr": "10.0.0.5/24"},
            ),
            await client.post(
                "/allocations",
                json={"tenant_id": str(tenant.id), "cidr": "0.0.0.0/0"},
            ),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert "10.0.0.0/24" in responses[1].text


async def test_revoke_and_overlap_check_endpoints(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, name="Revoke API Tenant")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await client.post(
            "/allocations",
            json={"tenant_id": str(tenant.id), "cidr": "10.100.0.0/24"},
        )
        overlap = await client.post("/allocations/overlap-check", json={"cidr": "10.100.0.10/32"})
        revoked = await client.post(f"/allocations/{created.json()['id']}/revoke")
        clear = await client.post("/allocations/overlap-check", json={"cidr": "10.100.0.10/32"})

    assert overlap.status_code == 200
    assert overlap.json()["overlaps"]
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert clear.status_code == 200
    assert not clear.json()["overlaps"]


async def test_tenant_self_view_returns_only_own_active_allocations(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    own_tenant = await create_tenant(db_session, name="Own API Tenant")
    other_tenant = await create_tenant(db_session, name="Other API Tenant")
    tenant_user = await create_tenant_user(db_session, username="own-api-user", tenant=own_tenant)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        own = await client.post(
            "/allocations",
            json={"tenant_id": str(own_tenant.id), "cidr": "10.110.0.0/24"},
        )
        await client.post(
            "/allocations",
            json={"tenant_id": str(other_tenant.id), "cidr": "10.111.0.0/24"},
        )
        await authenticate(client, store, tenant_user)
        response = await client.get("/me/allocations")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [own.json()["id"]]


async def test_tenant_self_read_foreign_allocation_is_zero_leak_404(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    own_tenant = await create_tenant(db_session, name="Read Own API Tenant")
    other_tenant = await create_tenant(db_session, name="Read Other API Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="read-own-api-user", tenant=own_tenant
    )

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        foreign = await client.post(
            "/allocations",
            json={"tenant_id": str(other_tenant.id), "cidr": "10.120.0.0/24"},
        )
        await authenticate(client, store, tenant_user)
        response = await client.get(f"/me/allocations/{foreign.json()['id']}")

    assert response.status_code == 404
    assert "10.120.0.0/24" not in response.text
    assert str(other_tenant.id) not in response.text


async def test_tenant_user_denied_on_admin_allocation_endpoints(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = await create_tenant(db_session, name="Denied API Tenant")
    tenant_user = await create_tenant_user(db_session, username="denied-api-user", tenant=tenant)

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        responses = [
            await client.get(f"/allocations?tenant_id={tenant.id}"),
            await client.post(
                "/allocations",
                json={"tenant_id": str(tenant.id), "cidr": "10.130.0.0/24"},
            ),
            await client.post("/allocations/overlap-check", json={"cidr": "10.130.0.0/24"}),
            await client.post(f"/allocations/{tenant.id}/revoke"),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]


async def test_allocate_to_suspended_tenant_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    suspended = await create_tenant(
        db_session,
        name="Suspended API Tenant",
        status=TenantStatus.suspended,
    )

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/allocations",
            json={"tenant_id": str(suspended.id), "cidr": "10.140.0.0/24"},
        )

    assert response.status_code == 409
