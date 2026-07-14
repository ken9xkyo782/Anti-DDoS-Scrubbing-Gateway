from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentJob,
    ChangeTrigger,
    FeedSyncOverlap,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    JobType,
    NodeControl,
    NodeHealthSnapshot,
    ProtectedService,
    ServicePlan,
    TelemetryCounter,
    TelemetryScope,
    Tenant,
    ThreatFeedSource,
    WhitelistEntry,
    XdpMode,
)
from app.worker.alert_sources import AlertSources

pytestmark = pytest.mark.integration


def counter(
    service: ProtectedService,
    *,
    window_start: datetime,
    clean_bps: int,
    drop_bytes: int,
) -> TelemetryCounter:
    return TelemetryCounter(
        scope=TelemetryScope.service,
        service_id=service.id,
        dp_id=service.dp_id,
        window_start=window_start,
        window_seconds=10,
        clean_pkts=10,
        clean_bytes=clean_bps * 10 // 8,
        drop_pkts=2,
        drop_bytes=drop_bytes,
        drop_by_reason={"rate_limit_drop": 2},
        pps=1,
        bps=clean_bps,
    )


async def test_loads_persisted_alert_source_snapshot(db_session: AsyncSession) -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    tenant = Tenant(name="Alert source tenant")
    service = ProtectedService(
        tenant=tenant,
        name="alert-source-edge",
        cidr_or_ip="203.0.113.10/32",
    )
    db_session.add_all([tenant, service])
    await db_session.flush()

    plan = ServicePlan(
        service_id=service.id,
        committed_clean_gbps=Decimal("10"),
        ceiling_clean_gbps=Decimal("20"),
    )
    source = ThreatFeedSource(
        name="Alert source failed feed",
        url="https://feeds.example.test/deny.txt",
        sync_interval_seconds=3600,
    )
    overlap_source = ThreatFeedSource(
        name="Alert source overlap feed",
        url="https://feeds.example.test/overlap.txt",
        sync_interval_seconds=3600,
    )
    db_session.add_all([plan, source, overlap_source])
    await db_session.flush()

    failed_run = FeedSyncRun(
        feed_source_id=source.id,
        source_name=source.name,
        sequence=1,
        trigger=ChangeTrigger.feed_manual,
        status=FeedSyncStatus.failed,
        finished_at=now - timedelta(seconds=5),
    )
    overlap_run = FeedSyncRun(
        feed_source_id=overlap_source.id,
        source_name=overlap_source.name,
        sequence=1,
        trigger=ChangeTrigger.feed_manual,
        status=FeedSyncStatus.success,
        finished_at=now - timedelta(seconds=1),
    )
    whitelist = WhitelistEntry(service_id=service.id, source_cidr="198.51.100.0/24")
    db_session.add_all([failed_run, overlap_run, whitelist])
    await db_session.flush()

    db_session.add_all(
        [
            NodeHealthSnapshot(
                captured_at=now - timedelta(seconds=120),
                window_seconds=10,
                xdp_mode=XdpMode.generic,
                active_slot=0,
                map_version=1,
                map_error_count=3,
                node_clean_bps=900,
                node_capacity_bps=1_000,
                bloom_stats={"bloom_hit_lpm_miss": 11},
            ),
            counter(
                service,
                window_start=now - timedelta(seconds=30),
                clean_bps=100,
                drop_bytes=50,
            ),
            counter(
                service,
                window_start=now - timedelta(seconds=10),
                clean_bps=800,
                drop_bytes=250,
            ),
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=1,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.queued,
                created_at=now - timedelta(seconds=20),
            ),
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=2,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.failed,
                created_at=now - timedelta(seconds=10),
            ),
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=3,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.applying,
                created_at=now - timedelta(seconds=90),
                started_at=now - timedelta(seconds=90),
            ),
            FeedSyncOverlap(
                feed_sync_run_id=overlap_run.id,
                feed_source_cidr="198.51.100.128/25",
                whitelist_entry_id=whitelist.id,
                service_id=service.id,
            ),
            NodeControl(bypass_enabled=True, maintenance_enabled=True),
        ]
    )
    await db_session.flush()

    inputs = await AlertSources(
        telemetry_stale_seconds=60,
        stuck_applying_seconds=60,
    ).load(db_session, now)

    assert inputs.node.map_error_count == 3
    assert inputs.node.xdp_mode == "generic"
    assert inputs.node.node_clean_bps == 900
    assert inputs.node.node_capacity_bps == 1_000
    assert inputs.node.bloom_false_positives == 11
    assert inputs.node.telemetry_stale is True
    assert inputs.node.job_backlog == 1
    assert inputs.node.apply_failed_count == 1
    assert inputs.node.stuck_applying is True
    assert inputs.node.feed_failure_count == 1
    assert inputs.node.bypass_enabled is True
    assert inputs.node.maintenance_enabled is True

    assert len(inputs.services) == 1
    loaded_service = inputs.services[0]
    assert loaded_service.scope_key == str(service.dp_id)
    assert loaded_service.tenant_id == tenant.id
    assert loaded_service.service_id == service.id
    assert loaded_service.clean_bps == 800
    assert loaded_service.drop_bps == 200
    assert loaded_service.total_bps == 1_000
    assert loaded_service.committed_bps == 10_000_000_000
    assert loaded_service.whitelist_overlap_count == 1


async def test_load_empty_database_returns_empty_inputs(db_session: AsyncSession) -> None:
    inputs = await AlertSources().load(db_session, datetime(2026, 7, 14, tzinfo=UTC))

    assert inputs.node.map_error_count is None
    assert inputs.node.telemetry_stale is None
    assert inputs.node.job_backlog == 0
    assert inputs.node.apply_failed_count == 0
    assert inputs.node.stuck_applying is False
    assert inputs.node.feed_failure_count == 0
    assert inputs.node.bypass_enabled is None
    assert inputs.node.maintenance_enabled is None
    assert inputs.services == ()


async def test_load_uses_health_age_to_mark_staleness(db_session: AsyncSession) -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    db_session.add(
        NodeHealthSnapshot(
            captured_at=now - timedelta(seconds=61),
            window_seconds=2,
            xdp_mode=XdpMode.native,
            active_slot=0,
            map_version=1,
            map_error_count=0,
            node_clean_bps=1,
            node_capacity_bps=2,
            bloom_stats={},
        )
    )
    await db_session.flush()

    inputs = await AlertSources(telemetry_stale_seconds=60).load(db_session, now)

    assert inputs.node.telemetry_stale is True
