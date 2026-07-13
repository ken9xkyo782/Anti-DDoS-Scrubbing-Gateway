from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    NodeHealthSnapshot,
    ProtectedService,
    TelemetryCounter,
    TelemetryScope,
    Tenant,
    XdpMode,
)
from app.worker.telemetry import TelemetryAggregator
from app.worker.telemetry_reader import (
    FakeTelemetryReader,
    NodeCounters,
    ServiceCounters,
    TelemetrySnapshot,
)

pytestmark = pytest.mark.integration


def snapshot(
    *,
    ts_ns: int,
    version: int = 1,
    services: tuple[ServiceCounters, ...],
    counters: dict[str, int] | None = None,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        ts_ns=ts_ns,
        active_slot=0,
        active_version=version,
        xdp_mode="native",
        xdp_prog_id=11,
        xdp_ifindex=7,
        node=NodeCounters(
            counters=counters or {"map_error": 0, "rate_limit_drop": 0},
            sample_stats={"sample_emitted": 0},
            bloom_stats={"global_blacklist": 0},
        ),
        services=services,
    )


def service_counters(
    dp_id: int,
    *,
    clean_pkts: int,
    clean_bytes: int,
    drop_pkts: int,
    drop_bytes: int,
) -> ServiceCounters:
    return ServiceCounters(
        dp_id=dp_id,
        clean_pkts=clean_pkts,
        clean_bytes=clean_bytes,
        drop_pkts=drop_pkts,
        drop_bytes=drop_bytes,
        drop_by_reason={"rate_limit_drop": drop_pkts},
    )


async def create_service(session_factory: async_sessionmaker[AsyncSession]) -> ProtectedService:
    async with session_factory() as db:
        service = ProtectedService(
            tenant=Tenant(name="Telemetry Aggregator Tenant"),
            name="telemetry-aggregator-edge",
            cidr_or_ip="203.0.113.20/32",
        )
        db.add(service)
        await db.commit()
        await db.refresh(service)
        return service


def aggregator(
    session_factory: async_sessionmaker[AsyncSession],
    snapshots: list[TelemetrySnapshot | None],
    *,
    retention_seconds: int = 60,
) -> TelemetryAggregator:
    return TelemetryAggregator(
        reader=FakeTelemetryReader(snapshots=snapshots),
        session_factory=session_factory,
        interval_seconds=2,
        retention_seconds=retention_seconds,
        node_clean_capacity_gbps=Decimal("40"),
    )


async def test_aggregator_persists_baseline_deltas_and_node_health(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(committed_db)
    first = snapshot(
        ts_ns=1_000_000_000,
        services=(
            service_counters(
                service.dp_id,
                clean_pkts=10,
                clean_bytes=1_000,
                drop_pkts=2,
                drop_bytes=100,
            ),
        ),
        counters={"map_error": 1, "rate_limit_drop": 2},
    )
    second = snapshot(
        ts_ns=3_000_000_000,
        services=(
            service_counters(
                service.dp_id,
                clean_pkts=15,
                clean_bytes=1_600,
                drop_pkts=3,
                drop_bytes=150,
            ),
        ),
        counters={"map_error": 1, "rate_limit_drop": 3},
    )
    telemetry = aggregator(committed_db, [first, second])

    await telemetry.aggregate_once()
    await telemetry.aggregate_once()

    async with committed_db() as db:
        service_rows = (
            (
                await db.execute(
                    select(TelemetryCounter)
                    .where(TelemetryCounter.scope == TelemetryScope.service)
                    .order_by(TelemetryCounter.created_at)
                )
            )
            .scalars()
            .all()
        )
        node_rows = (
            (
                await db.execute(
                    select(TelemetryCounter)
                    .where(TelemetryCounter.scope == TelemetryScope.node)
                    .order_by(TelemetryCounter.created_at)
                )
            )
            .scalars()
            .all()
        )
        health = (
            (await db.execute(select(NodeHealthSnapshot).order_by(NodeHealthSnapshot.captured_at)))
            .scalars()
            .all()
        )

    assert [(row.is_baseline, row.clean_pkts, row.clean_bytes) for row in service_rows] == [
        (True, 0, 0),
        (False, 5, 600),
    ]
    assert service_rows[-1].drop_by_reason == {"rate_limit_drop": 1}
    assert service_rows[-1].pps == 2
    assert service_rows[-1].bps == 2_400
    assert [(row.is_baseline, row.clean_pkts, row.drop_pkts) for row in node_rows] == [
        (True, 0, 0),
        (False, 5, 1),
    ]
    assert health[-1].xdp_mode is XdpMode.native
    assert health[-1].map_error_count == 1
    assert health[-1].node_clean_bps == 2_400
    assert health[-1].node_capacity_bps == 40_000_000_000


async def test_aggregator_treats_reset_as_raw_and_keeps_unknown_ids_in_node_totals(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(committed_db)
    first = snapshot(
        ts_ns=1_000_000_000,
        services=(
            service_counters(
                service.dp_id,
                clean_pkts=10,
                clean_bytes=1_000,
                drop_pkts=0,
                drop_bytes=0,
            ),
        ),
    )
    reset = snapshot(
        ts_ns=3_000_000_000,
        version=2,
        services=(
            service_counters(
                service.dp_id,
                clean_pkts=3,
                clean_bytes=300,
                drop_pkts=1,
                drop_bytes=20,
            ),
            service_counters(9_999, clean_pkts=7, clean_bytes=700, drop_pkts=2, drop_bytes=40),
        ),
        counters={"map_error": 0, "rate_limit_drop": 3},
    )
    telemetry = aggregator(committed_db, [first, reset])

    await telemetry.aggregate_once()
    await telemetry.aggregate_once()

    async with committed_db() as db:
        service_rows = (
            (
                await db.execute(
                    select(TelemetryCounter)
                    .where(TelemetryCounter.scope == TelemetryScope.service)
                    .order_by(TelemetryCounter.created_at)
                )
            )
            .scalars()
            .all()
        )
        node = (
            await db.execute(
                select(TelemetryCounter)
                .where(TelemetryCounter.scope == TelemetryScope.node)
                .order_by(TelemetryCounter.created_at.desc())
                .limit(1)
            )
        ).scalar_one()

    assert [(row.clean_pkts, row.clean_bytes) for row in service_rows] == [(0, 0), (3, 300)]
    assert node.clean_pkts == 10
    assert node.clean_bytes == 1_000
    assert node.drop_pkts == 3
    assert node.drop_bytes == 60


async def test_aggregator_prunes_expired_rows_and_records_offline_health(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        db.add(
            TelemetryCounter(
                scope=TelemetryScope.node,
                window_start=datetime.now(UTC) - timedelta(seconds=120),
                window_seconds=2,
                clean_pkts=0,
                clean_bytes=0,
                drop_pkts=0,
                drop_bytes=0,
                drop_by_reason={},
                pps=0,
                bps=0,
            )
        )
        db.add(
            NodeHealthSnapshot(
                captured_at=datetime.now(UTC) - timedelta(seconds=120),
                window_seconds=2,
                xdp_mode=XdpMode.native,
                active_slot=0,
                map_version=1,
                map_error_count=0,
                node_clean_bps=0,
                node_capacity_bps=40_000_000_000,
                bloom_stats={},
            )
        )
        await db.commit()

    telemetry = aggregator(committed_db, [None], retention_seconds=1)
    await telemetry.aggregate_once()

    async with committed_db() as db:
        counters = (await db.execute(select(TelemetryCounter))).scalars().all()
        health = (await db.execute(select(NodeHealthSnapshot))).scalars().all()

    assert counters == []
    assert len(health) == 1
    assert health[0].xdp_mode is XdpMode.offline
