from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    JobType,
    NodeHealthSnapshot,
    ProtectedService,
    Role,
    ServicePlan,
    TelemetryCounter,
    TelemetryScope,
    Tenant,
    ThreatFeedSource,
    User,
    XdpMode,
)
from app.db.session import get_db
from app.worker.telemetry_reader import FakeTelemetryReader

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
    app.dependency_overrides[telemetry.get_telemetry_reader] = lambda: FakeTelemetryReader(
        snapshots=[]
    )

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
    planned_empty_service = await create_service(
        db_session,
        tenant=owner_tenant,
        name="telemetry-api-planned-empty",
        cidr="203.0.113.84/32",
    )
    db_session.add_all(
        [
            ServicePlan(
                service_id=service.id,
                committed_clean_gbps=Decimal("1"),
                ceiling_clean_gbps=Decimal("1"),
            ),
            ServicePlan(
                service_id=planned_empty_service.id,
                committed_clean_gbps=Decimal("1"),
                ceiling_clean_gbps=Decimal("1"),
            ),
        ]
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
        planned_empty = await client.get(f"/services/{planned_empty_service.id}/telemetry")
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
        "committed_clean_bps": 1_000_000_000,
        "committed_honored": False,
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
        "committed_clean_bps": 0,
        "committed_honored": None,
        "window_start": None,
        "window_seconds": 0,
        "stale": True,
    }
    assert planned_empty.status_code == 200
    assert planned_empty.json() == {
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
        "committed_clean_bps": 1_000_000_000,
        "committed_honored": None,
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
    honored_service = await create_service(
        db_session,
        tenant=tenant,
        name="telemetry-node-honored-edge",
        cidr="203.0.113.83/32",
    )
    now = datetime.now(UTC)
    feed = ThreatFeedSource(
        name="Telemetry Feed",
        url="https://feeds.example.test/telemetry.txt",
        sync_interval_seconds=3_600,
        last_status=FeedSyncStatus.failed,
        last_error="upstream timeout",
        last_sync_at=now,
    )
    db_session.add(feed)
    await db_session.flush()
    older_run = FeedSyncRun(
        feed_source_id=feed.id,
        source_name=feed.name,
        sequence=1,
        trigger=ChangeTrigger.feed_schedule,
        status=FeedSyncStatus.success,
        finished_at=now - timedelta(seconds=10),
    )
    latest_run = FeedSyncRun(
        feed_source_id=feed.id,
        source_name=feed.name,
        sequence=2,
        trigger=ChangeTrigger.feed_schedule,
        status=FeedSyncStatus.failed,
        started_at=now - timedelta(seconds=3),
        finished_at=now,
        duration_ms=3_000,
        error="upstream timeout",
        valid=7,
        added=2,
        removed=1,
        skipped_invalid=3,
        overlap_count=4,
    )
    honored_counter = counter(
        scope=TelemetryScope.service,
        service=honored_service,
        window_start=now,
    )
    honored_counter.bps = 2_000_000_000
    latest_apply = AgentJob(
        target_type="service",
        target_id=service.id,
        version=3,
        job_type=JobType.service_update,
        trigger=ChangeTrigger.service,
        status=JobStatus.failed,
        error="apply helper failed",
        created_at=now + timedelta(seconds=1),
        started_at=now + timedelta(seconds=1),
        finished_at=now + timedelta(seconds=2),
    )
    db_session.add_all(
        [
            counter(scope=TelemetryScope.node, window_start=now),
            counter(scope=TelemetryScope.service, service=service, window_start=now),
            honored_counter,
            NodeHealthSnapshot(
                captured_at=now,
                window_seconds=2,
                xdp_mode=XdpMode.native,
                active_slot=1,
                map_version=7,
                map_error_count=3,
                node_clean_bps=4_000,
                node_capacity_bps=40_000_000_000,
                bloom_stats={"global_blacklist": 7, "service_blacklist": 2},
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
            latest_apply,
            ServicePlan(
                service_id=service.id,
                committed_clean_gbps=Decimal("1"),
                ceiling_clean_gbps=Decimal("1"),
            ),
            ServicePlan(
                service_id=honored_service.id,
                committed_clean_gbps=Decimal("2"),
                ceiling_clean_gbps=Decimal("2"),
            ),
            older_run,
            latest_run,
        ]
    )
    await db_session.flush()
    db_session.add(
        AgentJob(
            target_type="feed_sync_run",
            feed_sync_run_id=latest_run.id,
            version=latest_run.sequence,
            job_type=JobType.feed_sync,
            trigger=ChangeTrigger.feed_schedule,
            status=JobStatus.queued,
            created_at=now + timedelta(seconds=3),
        )
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
    expected_committed_services = sorted(
        [
            {
                "service_id": str(service.id),
                "observed_clean_bps": 4_000,
                "committed_clean_bps": 1_000_000_000,
                "honored": False,
                "window_start": now.isoformat().replace("+00:00", "Z"),
            },
            {
                "service_id": str(honored_service.id),
                "observed_clean_bps": 2_000_000_000,
                "committed_clean_bps": 2_000_000_000,
                "honored": True,
                "window_start": now.isoformat().replace("+00:00", "Z"),
            },
        ],
        key=lambda item: item["service_id"],
    )
    assert node_health.json() == {
        "has_data": True,
        "xdp_mode": "native",
        "active_slot": 1,
        "map_version": 7,
        "map_error_count": 3,
        "unresolved_services": 0,
        "node_clean_bps": 4_000,
        "node_capacity_bps": 40_000_000_000,
        "window_start": now.isoformat().replace("+00:00", "Z"),
        "window_seconds": 2,
        "stale": False,
        "bloom_stats": {"global_blacklist": 7, "service_blacklist": 2},
        "committed_services": expected_committed_services,
        "job_backlog": {"queued": 2, "applying": 1},
        "last_apply": {
            "id": str(latest_apply.id),
            "job_type": "SERVICE_UPDATE",
            "status": "failed",
            "error": "apply helper failed",
            "created_at": (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "started_at": (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "finished_at": (now + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        },
        "feed_sources": [
            {
                "id": str(feed.id),
                "name": "Telemetry Feed",
                "enabled": True,
                "last_status": "failed",
                "last_error": "upstream timeout",
                "last_sync_at": now.isoformat().replace("+00:00", "Z"),
                "last_run": {
                    "id": str(latest_run.id),
                    "sequence": 2,
                    "status": "failed",
                    "started_at": (now - timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                    "finished_at": now.isoformat().replace("+00:00", "Z"),
                    "duration_ms": 3_000,
                    "error": "upstream timeout",
                    "valid": 7,
                    "added": 2,
                    "removed": 1,
                    "skipped_invalid": 3,
                    "overlap_count": 4,
                },
            }
        ],
        "bypass": {
            "desired": False,
            "effective": False,
            "activated_at": None,
            "active_seconds": 0,
        },
        "maintenance": {
            "desired": False,
            "effective": False,
            "activated_at": None,
            "active_seconds": 0,
        },
        "bypass_pkts": 0,
        "bypass_bytes": 0,
    }


async def test_service_telemetry_history_and_export(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    owner_tenant = Tenant(name="Telemetry History Owner")
    other_tenant = Tenant(name="Telemetry History Other")
    db_session.add_all([owner_tenant, other_tenant])
    await db_session.flush()
    owner = await create_user(
        db_session,
        username="telemetry-history-owner",
        role=Role.tenant_user,
        tenant=owner_tenant,
    )
    other = await create_user(
        db_session,
        username="telemetry-history-other",
        role=Role.tenant_user,
        tenant=other_tenant,
    )
    service = await create_service(
        db_session,
        tenant=owner_tenant,
        name="telemetry-history-edge",
        cidr="203.0.113.90/32",
    )
    now = datetime.now(UTC)
    window_starts = [now - timedelta(seconds=4), now - timedelta(seconds=2), now]
    db_session.add(
        counter(
            scope=TelemetryScope.service,
            service=service,
            window_start=now - timedelta(seconds=6),
            baseline=True,
        )
    )
    for window_start in window_starts:
        db_session.add(
            counter(scope=TelemetryScope.service, service=service, window_start=window_start)
        )
    await db_session.flush()

    expected_iso = [start.isoformat().replace("+00:00", "Z") for start in window_starts]

    async for client in make_client(db_session, store):
        await authenticate(client, store, owner)
        history = await client.get(f"/services/{service.id}/telemetry/history")
        csv_export = await client.get(
            f"/services/{service.id}/telemetry/export", params={"format": "csv"}
        )
        json_export = await client.get(
            f"/services/{service.id}/telemetry/export", params={"format": "json"}
        )
        await authenticate(client, store, other)
        cross_history = await client.get(f"/services/{service.id}/telemetry/history")
        cross_export = await client.get(
            f"/services/{service.id}/telemetry/export", params={"format": "csv"}
        )

    assert history.status_code == 200
    body = history.json()
    assert body["has_data"] is True
    # The baseline window is excluded and the rest are chronological.
    assert [window["window_start"] for window in body["windows"]] == expected_iso
    assert cross_history.status_code == 404
    assert cross_export.status_code == 404

    assert json_export.status_code == 200
    assert json_export.json()["windows"] == body["windows"]

    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    csv_lines = csv_export.text.strip().split("\n")
    assert csv_lines[0] == (
        "window_start,window_seconds,clean_pkts,clean_bytes,drop_pkts,drop_bytes,pps,bps"
    )
    assert len(csv_lines) == 1 + len(window_starts)


async def test_node_telemetry_history_and_export_are_admin_only(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Telemetry Node History Tenant")
    db_session.add(tenant)
    await db_session.flush()
    admin = await create_user(
        db_session,
        username="telemetry-node-history-admin",
        role=Role.admin,
    )
    tenant_user = await create_user(
        db_session,
        username="telemetry-node-history-user",
        role=Role.tenant_user,
        tenant=tenant,
    )
    now = datetime.now(UTC)
    window_starts = [now - timedelta(seconds=2), now]
    db_session.add(
        counter(
            scope=TelemetryScope.node,
            window_start=now - timedelta(seconds=4),
            baseline=True,
        )
    )
    for window_start in window_starts:
        db_session.add(counter(scope=TelemetryScope.node, window_start=window_start))
    await db_session.flush()

    expected_iso = [start.isoformat().replace("+00:00", "Z") for start in window_starts]

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        denied_history = await client.get("/node/telemetry/history")
        denied_export = await client.get("/node/telemetry/export", params={"format": "csv"})
        await authenticate(client, store, admin)
        history = await client.get("/node/telemetry/history")
        csv_export = await client.get("/node/telemetry/export", params={"format": "csv"})

    assert denied_history.status_code == 403
    assert denied_export.status_code == 403
    assert history.status_code == 200
    assert [window["window_start"] for window in history.json()["windows"]] == expected_iso
    assert csv_export.status_code == 200
    csv_lines = csv_export.text.strip().split("\n")
    assert len(csv_lines) == 1 + len(window_starts)


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
    assert health_response.json()["bloom_stats"] == {}
    assert health_response.json()["committed_services"] == []
    assert health_response.json()["job_backlog"] == {"queued": 0, "applying": 0}
    assert health_response.json()["last_apply"] is None
    assert health_response.json()["feed_sources"] == []
