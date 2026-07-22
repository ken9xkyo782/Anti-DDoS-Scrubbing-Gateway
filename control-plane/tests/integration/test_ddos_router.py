from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import ddos
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import Role, Tenant, User
from app.db.session import get_db
from app.services.ddos_amplification import HARDCODED_AMP_PORTS

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(ddos.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "ddos-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant_user(db_session: AsyncSession) -> User:
    tenant = Tenant(name="DDoS Tenant")
    user = User(
        username="ddos-api-tenant-user",
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


async def test_get_amplification_config_admin(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.get("/ddos/amplification")
        assert response.status_code == 200
        data = response.json()

        assert data["hardcoded_ports"] == list(HARDCODED_AMP_PORTS)
        assert data["dynamic_ports"] == []


async def test_add_and_delete_blocked_udp_port_admin(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)

        # 1. Add port 9999
        post_res = await client.post(
            "/ddos/amplification/ports",
            json={"port": 9999, "note": "SSDP amplification"},
        )
        assert post_res.status_code == 201
        port_data = post_res.json()
        assert port_data["port"] == 9999
        assert port_data["note"] == "SSDP amplification"
        assert port_data["created_by"] == str(admin.id)

        # 2. GET amplification config shows dynamic port
        get_res = await client.get("/ddos/amplification")
        assert get_res.status_code == 200
        config_data = get_res.json()
        assert len(config_data["dynamic_ports"]) == 1
        assert config_data["dynamic_ports"][0]["port"] == 9999

        # 3. Add duplicate port 9999 -> 409
        dup_res = await client.post(
            "/ddos/amplification/ports",
            json={"port": 9999, "note": "Duplicate"},
        )
        assert dup_res.status_code == 409

        # 4. Add invalid port 70000 -> 422
        inv_res = await client.post(
            "/ddos/amplification/ports",
            json={"port": 70000},
        )
        assert inv_res.status_code == 422

        # 5. Delete port 9999 -> 204
        del_res = await client.delete("/ddos/amplification/ports/9999")
        assert del_res.status_code == 204

        # 6. Delete again -> 404
        del_absent = await client.delete("/ddos/amplification/ports/9999")
        assert del_absent.status_code == 404


async def test_ddos_router_tenant_forbidden(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant_user = await create_tenant_user(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)

        get_res = await client.get("/ddos/amplification")
        assert get_res.status_code == 403

        post_res = await client.post(
            "/ddos/amplification/ports",
            json={"port": 1234},
        )
        assert post_res.status_code == 403

        del_res = await client.delete("/ddos/amplification/ports/1234")
        assert del_res.status_code == 403
