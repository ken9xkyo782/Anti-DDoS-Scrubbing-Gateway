from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import global_blacklist
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import BlacklistEntry, Role, Tenant, User
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(global_blacklist.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "global-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant_user(db_session: AsyncSession) -> User:
    tenant = Tenant(name="Global Blacklist Tenant")
    user = User(
        username="global-api-tenant-user",
        role=Role.tenant_user,
        tenant=tenant,
        password_hash=hash_password("tenant-pass"),
    )
    db_session.add_all([tenant, user])
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def test_admin_add_global_blacklist_returns_manual_source(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post("/blacklist", json={"source_cidr": "185.0.0.0/8"})

    assert response.status_code == 201
    assert response.json()["service_id"] is None
    assert response.json()["scope"] == "global"
    assert response.json()["source"] == "manual"


async def test_admin_lists_global_blacklist_with_source_discriminator(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "global-api-list-admin")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await client.post("/blacklist", json={"source_cidr": "185.0.0.0/8"})
        listed = await client.get("/blacklist")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [created.json()["id"]]
    assert listed.json()[0]["source"] == "manual"


async def test_admin_deletes_global_blacklist_entry(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "global-api-delete-admin")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await client.post("/blacklist", json={"source_cidr": "185.0.0.0/8"})
        deleted = await client.delete("/blacklist", params={"source_cidr": "185.0.0.0/8"})

    assert deleted.status_code == 204
    assert (await db_session.execute(select(func.count(BlacklistEntry.id)))).scalar_one() == 0


async def test_tenant_user_global_blacklist_endpoints_are_403_no_side_effect(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant_user = await create_tenant_user(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        responses = [
            await client.post("/blacklist", json={"source_cidr": "185.0.0.0/8"}),
            await client.get("/blacklist"),
            await client.delete("/blacklist", params={"source_cidr": "185.0.0.0/8"}),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert (await db_session.execute(select(func.count(BlacklistEntry.id)))).scalar_one() == 0
