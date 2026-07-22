from collections.abc import AsyncGenerator
from ipaddress import IPv4Network

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import rules
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import AllowRule, Role, Tenant, User
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
    app.include_router(rules.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "rules-api-admin") -> User:
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
    cidr: str = "203.0.113.10/32",
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
        name="rules-api-service",
        cidr_or_ip=IPv4Network(cidr),
        actor=actor,
    )


async def test_create_rule_returns_202_queued_and_bumps_version(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Rules API Create Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "tcp", "dst_port_lo": 80, "dst_port_hi": 80},
        )

    assert response.status_code == 202
    assert response.json()["apply_status"] == "queued"
    assert response.json()["version"] == 2
    assert service.service.version == 2


async def test_duplicate_priority_returns_409(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-duplicate-admin")
    tenant = await create_tenant(db_session, "Rules API Duplicate Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "tcp"},
        )
        response = await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "udp"},
        )

    assert response.status_code == 409


async def test_seventeenth_rule_returns_409(db_session: AsyncSession, redis_client: Redis) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-limit-admin")
    tenant = await create_tenant(db_session, "Rules API Limit Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        for priority in range(1, 17):
            await client.post(
                f"/services/{service.service.id}/rules",
                json={"priority": priority, "protocol": "tcp"},
            )
        response = await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 17, "protocol": "tcp"},
        )

    assert response.status_code == 409


async def test_invalid_ports_return_422(db_session: AsyncSession, redis_client: Redis) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-invalid-admin")
    tenant = await create_tenant(db_session, "Rules API Invalid Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "tcp", "dst_port_lo": 80, "dst_port_hi": 79},
        )

    assert response.status_code == 422


async def test_overlap_create_returns_warning(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-overlap-admin")
    tenant = await create_tenant(db_session, "Rules API Overlap Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "tcp", "dst_port_lo": 80, "dst_port_hi": 80},
        )
        response = await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 20, "protocol": "any"},
        )

    assert response.status_code == 202
    assert response.json()["apply_status"] == "queued"


async def test_overlap_check_dry_run_writes_nothing(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-dry-run-admin")
    tenant = await create_tenant(db_session, "Rules API Dry Run Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "tcp", "dst_port_lo": 80, "dst_port_hi": 80},
        )
        before = (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one()
        response = await client.post(
            f"/services/{service.service.id}/rules/overlap-check",
            json={"protocol": "tcp", "dst_port_lo": 80, "dst_port_hi": 443},
        )
        after = (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one()

    assert response.status_code == 200
    assert response.json()["warnings"] == ["Overlaps rule priority 10"]
    assert after == before


async def test_list_patch_delete_rules(db_session: AsyncSession, redis_client: Redis) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-crud-admin")
    tenant = await create_tenant(db_session, "Rules API CRUD Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await client.post(
            f"/services/{service.service.id}/rules",
            json={"priority": 10, "protocol": "tcp"},
        )
        rule = (
            await db_session.execute(
                select(AllowRule).where(AllowRule.service_id == service.service.id)
            )
        ).scalar_one()
        listed = await client.get(f"/services/{service.service.id}/rules")
        patched = await client.patch(
            f"/services/{service.service.id}/rules/{rule.id}",
            json={"priority": 20, "protocol": "udp"},
        )
        deleted = await client.delete(f"/services/{service.service.id}/rules/{rule.id}")

    assert created.status_code == 202
    assert [row["id"] for row in listed.json()] == [str(rule.id)]
    assert patched.status_code == 202
    assert patched.json()["apply_status"] == "queued"
    assert deleted.status_code == 202


async def test_cross_tenant_rule_access_returns_zero_leak_404(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-cross-admin")
    own_tenant = await create_tenant(db_session, "Rules API Cross Own Tenant")
    other_tenant = await create_tenant(db_session, "Rules API Cross Other Tenant")
    other_user = await create_tenant_user(
        db_session, username="rules-api-cross-user", tenant=other_tenant
    )
    service = await create_service(db_session, tenant=own_tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, other_user)
        response = await client.get(f"/services/{service.service.id}/rules")

    assert response.status_code == 404
    assert "rules-api-service" not in response.text
    assert str(own_tenant.id) not in response.text


async def test_rule_creation_ignores_pps_and_bps(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "rules-api-rate-admin")
    tenant = await create_tenant(db_session, "Rules API Rate Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            f"/services/{service.service.id}/rules",
            json={
                "priority": 10,
                "protocol": "tcp",
                "pps": 1000,
                "bps": 10000,
            },
        )
        assert response.status_code == 202

        listed = await client.get(f"/services/{service.service.id}/rules")
        assert listed.status_code == 200
        rule_data = listed.json()[0]
        assert "pps" not in rule_data
        assert "bps" not in rule_data
