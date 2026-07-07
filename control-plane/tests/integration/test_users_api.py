import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.users import router
from app.core.config import get_settings
from app.core.deps import (
    Principal,
    authorize_tenant_resource,
    get_current_user,
    get_session_store,
)
from app.core.security import hash_password, verify_password
from app.core.sessions import RedisSessionStore
from app.db.models import AuditEvent, Role, Tenant, User
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)

    @app.get("/tenant-resource/{tenant_id}")
    async def tenant_resource(
        tenant_id: uuid.UUID,
        principal: Annotated[Principal, Depends(get_current_user)],
    ) -> dict[str, str]:
        try:
            authorize_tenant_resource(principal, tenant_id)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        return {"tenant_id": str(tenant_id), "value": "own-resource"}

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant_user(
    db_session: AsyncSession,
    *,
    username: str,
    tenant: Tenant | None = None,
) -> User:
    tenant = tenant or Tenant(name=f"{username}-tenant")
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash=hash_password("tenant-pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> str:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)
    return sid


async def test_tenant_user_denied_on_users_endpoints(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant_user = await create_tenant_user(db_session, username="tenant-denied")
    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)

        responses = [
            await client.get("/users"),
            await client.post(
                "/users",
                json={"username": "x", "password": "p", "role": "admin", "tenant_id": None},
            ),
            await client.patch(f"/users/{tenant_user.id}", json={"username": "x"}),
            await client.delete(f"/users/{tenant_user.id}"),
            await client.post(
                f"/users/{tenant_user.id}/reset-password",
                json={"new_password": "p"},
            ),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]


async def test_admin_create_user_persists_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = Tenant(name="Customer A")
    db_session.add(tenant)
    await db_session.flush()
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/users",
            json={
                "username": "created-api",
                "password": "created-pass",
                "role": "tenant_user",
                "tenant_id": str(tenant.id),
            },
        )

    assert response.status_code == 201
    assert response.json()["username"] == "created-api"
    assert response.json()["tenant_id"] == str(tenant.id)
    created = (
        await db_session.execute(select(User).where(User.username == "created-api"))
    ).scalar_one()
    assert verify_password("created-pass", created.password_hash)
    assert (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.create"))
    ).scalar_one()


async def test_admin_list_includes_tenant_labels(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = Tenant(name="Tenant Label")
    tenant_user = await create_tenant_user(db_session, username="listed-user", tenant=tenant)
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.get("/users")

    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == str(tenant_user.id))
    assert row["tenant_name"] == "Tenant Label"


async def test_admin_patch_user_updates_role(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, username="admin-a")
    target = await create_admin(db_session, username="admin-b")
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.patch(f"/users/{target.id}", json={"username": "renamed-admin"})

    assert response.status_code == 200
    assert response.json()["username"] == "renamed-admin"


async def test_admin_delete_user_removes_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, username="admin-a")
    target = await create_admin(db_session, username="delete-api")
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.delete(f"/users/{target.id}")

    assert response.status_code == 204
    assert await db_session.get(User, target.id) is None
    assert (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.delete"))
    ).scalar_one()


async def test_admin_reset_password_invalidates_target_sessions(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    target = await create_admin(db_session, username="reset-api")
    target_sid = await store.create(
        user_id=target.id,
        session_version=target.session_version,
        ip=None,
    )
    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/users/{target.id}/reset-password",
            json={"new_password": "reset-api-pass"},
        )

    assert response.status_code == 204
    assert await store.get(target_sid) is None
    await db_session.refresh(target)
    assert verify_password("reset-api-pass", target.password_hash)


async def test_isolation_pair_returns_404_without_other_tenant_data(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    own_user = await create_tenant_user(db_session, username="tenant-a")
    other_user = await create_tenant_user(db_session, username="tenant-b")
    assert own_user.tenant_id is not None
    assert other_user.tenant_id is not None
    async for client in make_client(db_session, store):
        await authenticate(client, store, own_user)
        response = await client.get(f"/tenant-resource/{other_user.tenant_id}")

    assert response.status_code == 404
    assert "tenant-b" not in response.text
    assert str(other_user.tenant_id) not in response.text
