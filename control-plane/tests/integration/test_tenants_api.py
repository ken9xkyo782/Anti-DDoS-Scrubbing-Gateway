from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import auth, tenants
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import AuditEvent, Role, Tenant, TenantStatus, User
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(tenants.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "tenant-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant_user(
    db_session: AsyncSession,
    *,
    username: str = "tenant-api-user",
    password: str = "tenant-pass",
    tenant: Tenant | None = None,
) -> User:
    tenant = tenant or Tenant(name=f"{username}-tenant")
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash=hash_password(password),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def test_admin_create_list_get_and_audit(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await client.post("/tenants", json={"name": "API Tenant"})
        listed = await client.get("/tenants")
        fetched = await client.get(f"/tenants/{created.json()['id']}")

    assert created.status_code == 201
    assert created.json()["status"] == "active"
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "API Tenant"
    assert any(row["name"] == "API Tenant" for row in listed.json())
    assert (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "tenant.create"))
    ).scalar_one()


async def test_admin_patch_suspend_and_reactivate(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = Tenant(name="Patch API Tenant")
    db_session.add(tenant)
    await db_session.flush()
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        patched = await client.patch(f"/tenants/{tenant.id}", json={"name": "Patched API Tenant"})
        suspended = await client.post(f"/tenants/{tenant.id}/suspend")
        reactivated = await client.post(f"/tenants/{tenant.id}/reactivate")

    assert patched.status_code == 200
    assert patched.json()["name"] == "Patched API Tenant"
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"


async def test_create_duplicate_tenant_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    db_session.add(Tenant(name="Duplicate API Tenant"))
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post("/tenants", json={"name": "duplicate api tenant"})

    assert response.status_code == 409


async def test_tenant_user_denied_on_tenant_endpoints(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant_user = await create_tenant_user(db_session)
    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        responses = [
            await client.get("/tenants"),
            await client.post("/tenants", json={"name": "Forbidden Tenant"}),
            await client.patch(f"/tenants/{tenant_user.tenant_id}", json={"name": "Forbidden"}),
            await client.post(f"/tenants/{tenant_user.tenant_id}/suspend"),
            await client.delete(f"/tenants/{tenant_user.tenant_id}"),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]


async def test_delete_with_dependents_returns_409_and_empty_delete_returns_204(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    blocked_user = await create_tenant_user(db_session, username="blocked-delete-user")
    empty_tenant = Tenant(name="Empty API Tenant")
    db_session.add(empty_tenant)
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        blocked = await client.delete(f"/tenants/{blocked_user.tenant_id}")
        deleted = await client.delete(f"/tenants/{empty_tenant.id}")

    assert blocked.status_code == 409
    assert "users" in blocked.text
    assert deleted.status_code == 204
    assert await db_session.get(Tenant, empty_tenant.id) is None


async def test_suspend_blocks_existing_tenant_user_session_and_reactivate_allows_login(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = Tenant(name="Suspend Login Tenant")
    tenant_user = await create_tenant_user(
        db_session,
        username="suspend-login-user",
        tenant=tenant,
    )

    async for client in make_client(db_session, store):
        login = await client.post(
            "/auth/login",
            json={"username": tenant_user.username, "password": "tenant-pass"},
        )
        assert login.status_code == 200
        assert (await client.get("/auth/me")).status_code == 200

        await authenticate(client, store, admin)
        suspend = await client.post(f"/tenants/{tenant.id}/suspend")
        assert suspend.status_code == 200

        client.cookies.clear()
        tenant_sid = await store.create(
            user_id=tenant_user.id,
            session_version=tenant_user.session_version,
            ip=None,
        )
        client.cookies.set(get_settings().session_cookie_name, tenant_sid)
        assert (await client.get("/auth/me")).status_code == 401

        await authenticate(client, store, admin)
        reactivate = await client.post(f"/tenants/{tenant.id}/reactivate")
        assert reactivate.status_code == 200
        client.cookies.clear()
        relogin = await client.post(
            "/auth/login",
            json={"username": tenant_user.username, "password": "tenant-pass"},
        )

    assert relogin.status_code == 200
    await db_session.refresh(tenant)
    assert tenant.status == TenantStatus.active
