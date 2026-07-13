from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import telemetry
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import (
    AgentJob,
    ChangeTrigger,
    FeedSyncStatus,
    JobStatus,
    JobType,
    NodeHealthSnapshot,
    ProtectedService,
    Role,
    TelemetryCounter,
    TelemetryScope,
    Tenant,
    ThreatFeedSource,
    User,
    XdpMode,
)
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(telemetry.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def create_user(
    db_session: AsyncSession,
    *,
    username: str,
    role: Role,
    tenant: Tenant | None = None,
) -> User:
    user = User(
        username=username,
        role=role,
        tenant=tenant,
        password_hash=hash_password("telemetry-api-pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    name: str,
    cidr: str,
) -> ProtectedService:
    service = ProtectedService(tenant=tenant, name=name, cidr_or_ip=cidr)
    db_session.add(service)
    await db_session.flush()
    return service


def counter(
    *,
    scope: TelemetryScope,
    window_start: datetime,
    service: ProtectedService | None = None,
    baseline: bool = False,
) -> TelemetryCounter:
    return TelemetryCounter(
        scope=scope,
        service_id=service.id if service is not None else None,
        dp_id=service.dp_id if service is not None else None,
        window_start=window_start,
        window_seconds=2,
        clean_pkts=100,
        clean_bytes=1_000,
        drop_pkts=5,
        drop_bytes=50,
        drop_by_reason={"rate_limit_drop": 5},
        pps=50,
        bps=4_000,
        top_dst_ports=[{"port": 443, "count": 4}],
        top_src=[{"ip": "198.51.100.10", "count": 4}],
        is_baseline=baseline,
    )


async def test_service_telemetry_is_tenant_scoped_and_zeroed_when_empty(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    owner_tenant = Tenant(name="Telemetry API Owner")
    other_tenant = Tenant(name="Telemetry API Other")
    db_session.add_all([owner_tenant, other_tenant])
    await db_session.flush()
    owner = await create_user(
        db_session,
        username="telemetry-api-owner",
        role=Role.tenant_user,
        tenant=owner_tenant,
    )
    other = await create_user(
        db_session,
        username="telemetry-api-other",
        role=Role.tenant_user,
        tenant=other_tenant,
    )
    service = await create_service(
        db_session,
        tenant=owner_tenant,
        name="telemetry-api-edge",
        cidr="203.0.113.80/32",
    )
    empty_service = await create_service(
        db_session,
        tenant=owner_tenant,
        name="telemetry-api-empty",
        cidr="203.0.113.81/32",
    )
    now = datetime.now(UTC)
    db_session.add_all(
        [
            counter(
                scope=TelemetryScope.service,
                service=service,
                window_start=now - timedelta(seconds=2),
                baseline=True,
            ),
            counter(scope=TelemetryScope.service, service=service, window_start=now),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, owner)
        owned = await client.get(f"/services/{service.id}/telemetry")
        empty = await client.get(f"/services/{empty_service.id}/telemetry")
        await authenticate(client, store, other)
        cross_tenant = await client.get(f"/services/{service.id}/telemetry")

    assert owned.status_code == 200
    assert owned.json() == {
        "has_data": True,
        "clean_pkts": 100,
        "clean_bytes": 1_000,
        "drop_pkts": 5,
        "drop_bytes": 50,
        "drop_by_reason": {"rate_limit_drop": 5},
        "pps": 50,
        "bps": 4_000,
        "top_dst_ports": [{"port": 443, "count": 4}],
        "top_src": [{"ip": "198.51.100.10", "count": 4}],
        "window_start": now.isoformat().replace("+00:00", "Z"),
        "window_seconds": 2,
        "stale": False,
    }
    assert cross_tenant.status_code == 404
    assert empty.status_code == 200
    assert empty.json() == {
        "has_data": False,
        "clean_pkts": 0,
        "clean_bytes": 0,
        "drop_pkts": 0,
        "drop_bytes": 0,
        "drop_by_reason": {},
        "pps": 0,
        "bps": 0,
        "top_dst_ports": [],
        "top_src": [],
        "window_start": None,
        "window_seconds": 0,
        "stale": True,
    }


async def test_node_telemetry_and_health_are_admin_only_and_expose_live_state(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Telemetry Node API Tenant")
    db_session.add(tenant)
    await db_session.flush()
    admin = await create_user(
        db_session,
        username="telemetry-node-admin",
        role=Role.admin,
    )
    tenant_user = await create_user(
        db_session,
        username="telemetry-node-user",
        role=Role.tenant_user,
        tenant=tenant,
    )
    service = await create_service(
        db_session,
        tenant=tenant,
        name="telemetry-node-edge",
        cidr="203.0.113.82/32",
    )
    now = datetime.now(UTC)
    feed = ThreatFeedSource(
        name="Telemetry Feed",
        url="https://feeds.example.test/telemetry.txt",
        sync_interval_seconds=3_600,
        last_status=FeedSyncStatus.success,
        last_sync_at=now,
    )
    db_session.add_all(
        [
            counter(scope=TelemetryScope.node, window_start=now),
            NodeHealthSnapshot(
                captured_at=now,
                window_seconds=2,
                xdp_mode=XdpMode.native,
                active_slot=1,
                map_version=7,
                map_error_count=3,
                node_clean_bps=4_000,
                node_capacity_bps=40_000_000_000,
                bloom_stats=None,
            ),
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=1,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.queued,
            ),
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=2,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.applying,
            ),
            feed,
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        telemetry_denied = await client.get("/node/telemetry")
        health_denied = await client.get("/node/health")
        await authenticate(client, store, admin)
        node_telemetry = await client.get("/node/telemetry")
        node_health = await client.get("/node/health")

    assert telemetry_denied.status_code == 403
    assert health_denied.status_code == 403
    assert node_telemetry.status_code == 200
    assert node_telemetry.json()["has_data"] is True
    assert node_telemetry.json()["clean_bytes"] == 1_000
    assert node_telemetry.json()["top_dst_ports"] == [{"port": 443, "count": 4}]
    assert node_telemetry.json()["top_src"] == [{"ip": "198.51.100.10", "count": 4}]
    assert node_telemetry.json()["stale"] is False
    assert node_health.status_code == 200
    assert node_health.json() == {
        "has_data": True,
        "xdp_mode": "native",
        "active_slot": 1,
        "map_version": 7,
        "map_error_count": 3,
        "node_clean_bps": 4_000,
        "node_capacity_bps": 40_000_000_000,
        "window_start": now.isoformat().replace("+00:00", "Z"),
        "window_seconds": 2,
        "stale": False,
        "job_backlog": {"queued": 1, "applying": 1},
        "feed_sources": [
            {
                "id": str(feed.id),
                "name": "Telemetry Feed",
                "enabled": True,
                "last_status": "success",
                "last_sync_at": now.isoformat().replace("+00:00", "Z"),
            }
        ],
    }


async def test_node_endpoints_return_stale_zeroed_empty_payloads(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_user(
        db_session,
        username="telemetry-empty-admin",
        role=Role.admin,
    )

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        telemetry_response = await client.get("/node/telemetry")
        health_response = await client.get("/node/health")

    assert telemetry_response.status_code == 200
    assert telemetry_response.json()["has_data"] is False
    assert telemetry_response.json()["window_start"] is None
    assert telemetry_response.json()["window_seconds"] == 0
    assert telemetry_response.json()["stale"] is True
    assert telemetry_response.json()["top_dst_ports"] == []
    assert telemetry_response.json()["top_src"] == []
    assert health_response.status_code == 200
    assert health_response.json()["has_data"] is False
    assert health_response.json()["window_start"] is None
    assert health_response.json()["window_seconds"] == 0
    assert health_response.json()["stale"] is True
    assert health_response.json()["job_backlog"] == {"queued": 0, "applying": 0}
    assert health_response.json()["feed_sources"] == []
