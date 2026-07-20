from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import telemetry
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import NodeControl, Role, Tenant, User
from app.db.session import get_db
from app.services.node_control import set_bypass, set_maintenance
from app.worker.telemetry_reader import FakeTelemetryReader, NodeCounters, TelemetrySnapshot

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
    reader: FakeTelemetryReader,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(telemetry.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[telemetry.get_telemetry_reader] = lambda: reader

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
        password_hash=hash_password("node-router-pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


def telemetry_snapshot(*, bypass_active: bool) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        ts_ns=1_000_000_000,
        active_slot=1,
        active_version=9,
        xdp_mode="native",
        xdp_prog_id=42,
        xdp_ifindex=7,
        node=NodeCounters(counters={}, sample_stats={}, bloom_stats={}),
        services=(),
        bypass_active=bypass_active,
        bypass_pkts=123,
        bypass_bytes=45_600,
    )


async def test_admin_can_toggle_independent_node_controls_and_read_live_health(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_user(db_session, username="node-router-admin", role=Role.admin)
    reader = FakeTelemetryReader(
        snapshots=[
            telemetry_snapshot(bypass_active=False),
            telemetry_snapshot(bypass_active=True),
            telemetry_snapshot(bypass_active=True),
        ]
    )

    async for client in make_client(db_session, store, reader):
        await authenticate(client, store, admin)
        bypass = await client.post("/node/bypass", json={"enabled": True, "reason": "incident"})
        maintenance = await client.post("/node/maintenance", json={"enabled": True})
        health = await client.get("/node/health")

    assert bypass.status_code == 200
    assert bypass.json()["desired"] is True
    assert bypass.json()["effective"] is False
    assert bypass.json()["activated_at"] is not None
    assert maintenance.status_code == 200
    assert maintenance.json()["desired"] is True
    assert maintenance.json()["effective"] is True
    assert health.status_code == 200
    assert health.json()["bypass"]["desired"] is True
    assert health.json()["bypass"]["effective"] is True
    assert health.json()["bypass"]["activated_at"] == bypass.json()["activated_at"]
    assert health.json()["bypass"]["active_seconds"] >= 0
    assert health.json()["maintenance"]["desired"] is True
    assert health.json()["maintenance"]["effective"] is True
    assert health.json()["maintenance"]["activated_at"] is not None
    assert health.json()["maintenance"]["active_seconds"] >= 0
    assert health.json()["xdp_mode"] == "native"
    assert health.json()["active_slot"] == 1
    assert health.json()["map_version"] == 9
    assert health.json()["bypass_pkts"] == 123
    assert health.json()["bypass_bytes"] == 45_600


async def test_node_routes_deny_non_admin_without_creating_control_state(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Node Router Tenant")
    db_session.add(tenant)
    await db_session.flush()
    tenant_user = await create_user(
        db_session,
        username="node-router-tenant",
        role=Role.tenant_user,
        tenant=tenant,
    )

    async for client in make_client(db_session, store, FakeTelemetryReader(snapshots=[])):
        await authenticate(client, store, tenant_user)
        bypass = await client.post("/node/bypass", json={"enabled": True})
        maintenance = await client.post("/node/maintenance", json={"enabled": True})
        health = await client.get("/node/health")

    assert bypass.status_code == 403
    assert maintenance.status_code == 403
    assert health.status_code == 403
    assert await db_session.scalar(select(func.count(NodeControl.id))) == 0


async def test_bypass_rejects_reasons_longer_than_512_characters(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_user(db_session, username="node-router-length", role=Role.admin)

    async for client in make_client(db_session, store, FakeTelemetryReader(snapshots=[])):
        await authenticate(client, store, admin)
        response = await client.post("/node/bypass", json={"enabled": True, "reason": "x" * 513})

    assert response.status_code == 422
    assert await db_session.scalar(select(func.count(NodeControl.id))) == 0


async def test_health_exposes_desired_and_effective_bypass_drift_when_reader_is_offline(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_user(db_session, username="node-router-drift", role=Role.admin)
    await set_bypass(db_session, actor=admin, enabled=True, reason="worker offline", ip=None)
    await set_maintenance(db_session, actor=admin, enabled=True, ip=None)

    async for client in make_client(db_session, store, FakeTelemetryReader(snapshots=[None])):
        await authenticate(client, store, admin)
        response = await client.get("/node/health")

    assert response.status_code == 200
    assert response.json()["bypass"]["desired"] is True
    assert response.json()["bypass"]["effective"] is False
    assert response.json()["maintenance"]["desired"] is True
    assert response.json()["maintenance"]["effective"] is True
    assert response.json()["xdp_mode"] == "offline"


async def test_node_health_reports_unresolved_services_count(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    from app.db.models import ProtectedService
    from app.worker.telemetry_reader import NextHopEntry

    store = make_store(redis_client)
    tenant = Tenant(name="Unresolved Services Tenant")
    db_session.add(tenant)
    await db_session.flush()
    admin = await create_user(db_session, username="node-unresolved-admin", role=Role.admin)

    svc1 = ProtectedService(
        tenant_id=tenant.id,
        name="svc-resolved",
        cidr_or_ip="203.0.113.10/32",
        dp_id=10,
        enabled=True,
    )
    svc2 = ProtectedService(
        tenant_id=tenant.id,
        name="svc-unresolved",
        cidr_or_ip="203.0.113.11/32",
        dp_id=11,
        enabled=True,
    )
    db_session.add_all([svc1, svc2])
    await db_session.flush()

    snapshot_mixed = TelemetrySnapshot(
        ts_ns=1_000_000_000,
        active_slot=1,
        active_version=1,
        xdp_mode="native",
        xdp_prog_id=1,
        xdp_ifindex=1,
        node=NodeCounters(counters={}, sample_stats={}, bloom_stats={}),
        services=(),
        nexthops=(
            NextHopEntry(
                dp_id=10,
                dst_mac="00:11:22:33:44:55",
                src_mac="aa:bb:cc:dd:ee:ff",
                resolved=True,
                age_s=5,
            ),
            NextHopEntry(
                dp_id=11,
                dst_mac="00:00:00:00:00:00",
                src_mac="aa:bb:cc:dd:ee:ff",
                resolved=False,
                age_s=0,
            ),
        ),
    )
    snapshot_all_resolved = TelemetrySnapshot(
        ts_ns=2_000_000_000,
        active_slot=1,
        active_version=1,
        xdp_mode="native",
        xdp_prog_id=1,
        xdp_ifindex=1,
        node=NodeCounters(counters={}, sample_stats={}, bloom_stats={}),
        services=(),
        nexthops=(
            NextHopEntry(
                dp_id=10,
                dst_mac="00:11:22:33:44:55",
                src_mac="aa:bb:cc:dd:ee:ff",
                resolved=True,
                age_s=5,
            ),
            NextHopEntry(
                dp_id=11,
                dst_mac="00:11:22:33:44:56",
                src_mac="aa:bb:cc:dd:ee:ff",
                resolved=True,
                age_s=2,
            ),
        ),
    )

    reader = FakeTelemetryReader(snapshots=[snapshot_mixed, snapshot_all_resolved])

    async for client in make_client(db_session, store, reader):
        await authenticate(client, store, admin)
        res_mixed = await client.get("/node/health")
        res_resolved = await client.get("/node/health")

    assert res_mixed.status_code == 200
    assert res_mixed.json()["unresolved_services"] == 1
    assert res_resolved.status_code == 200
    assert res_resolved.json()["unresolved_services"] == 0
