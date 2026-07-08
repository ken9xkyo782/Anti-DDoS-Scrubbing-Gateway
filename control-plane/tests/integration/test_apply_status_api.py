from collections.abc import AsyncGenerator
from ipaddress import IPv4Network

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import apply_status
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import AgentJob, Role, Tenant, User
from app.db.session import get_db
from app.services import allocations as allocation_service
from app.services import apply as apply_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(apply_status.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession) -> User:
    user = User(username="apply-api-admin", role=Role.admin, password_hash=hash_password("pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant_user(
    db_session: AsyncSession,
    tenant: Tenant,
    username: str,
) -> User:
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash=hash_password("pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def create_service(
    db_session: AsyncSession,
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
        name="apply-api-service",
        cidr_or_ip=IPv4Network("203.0.113.10/32"),
        actor=actor,
    )


async def test_apply_status_read_is_tenant_scoped_and_jobs_are_admin_only(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = Tenant(name="Apply API Tenant")
    other_tenant = Tenant(name="Apply API Other Tenant")
    db_session.add_all([tenant, other_tenant])
    await db_session.flush()
    owner = await create_tenant_user(db_session, tenant, "apply-api-owner")
    other = await create_tenant_user(db_session, other_tenant, "apply-api-other")
    service = await create_service(db_session, tenant, admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, owner)
        own = await client.get(f"/services/{service.service.id}/apply-status")
        jobs_denied = await client.get("/jobs")
        await authenticate(client, store, other)
        cross = await client.get(f"/services/{service.service.id}/apply-status")
        await authenticate(client, store, admin)
        jobs = await client.get("/jobs", params={"status": "queued"})

    assert own.status_code == 200
    assert own.json()["apply_status"] == "queued"
    assert own.json()["latest_job"]["status"] == "queued"
    assert cross.status_code == 404
    assert jobs_denied.status_code == 403
    assert jobs.status_code == 200
    assert jobs.json()[0]["target_id"] == str(service.service.id)


async def test_retry_failed_apply_returns_202_and_non_failed_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = Tenant(name="Apply API Retry Tenant")
    db_session.add(tenant)
    await db_session.flush()
    service = await create_service(db_session, tenant, admin)
    job = (
        await db_session.execute(select(AgentJob).where(AgentJob.target_id == service.service.id))
    ).scalar_one()
    await apply_service.mark_applying(db_session, job.id)
    await apply_service.mark_failed(db_session, job.id, "build failed")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        retried = await client.post(f"/services/{service.service.id}/apply-status/retry")
        retry_again = await client.post(f"/services/{service.service.id}/apply-status/retry")

    assert retried.status_code == 202
    assert retried.json()["apply_status"] == "queued"
    assert retry_again.status_code == 409
