from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BillingSample, ProtectedService, Tenant
from app.worker.billing import BillingMeter
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
    services: tuple[ServiceCounters, ...],
    version: int = 1,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        ts_ns=ts_ns,
        active_slot=0,
        active_version=version,
        xdp_mode="native",
        xdp_prog_id=11,
        xdp_ifindex=7,
        node=NodeCounters(counters={}, sample_stats={}, bloom_stats={}),
        services=services,
    )


def service_counters(dp_id: int, clean_bytes: int) -> ServiceCounters:
    return ServiceCounters(
        dp_id=dp_id,
        clean_pkts=0,
        clean_bytes=clean_bytes,
        drop_pkts=0,
        drop_bytes=0,
        drop_by_reason={},
    )


async def create_service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    cidr_or_ip: str,
) -> ProtectedService:
    async with session_factory() as db:
        service = ProtectedService(
            tenant=Tenant(name=f"{name} tenant"),
            name=name,
            cidr_or_ip=cidr_or_ip,
        )
        db.add(service)
        await db.commit()
        await db.refresh(service)
        return service


async def samples_for(
    session_factory: async_sessionmaker[AsyncSession], service_id: object
) -> list[BillingSample]:
    async with session_factory() as db:
        return list(
            (
                await db.scalars(
                    select(BillingSample)
                    .where(BillingSample.service_id == service_id)
                    .order_by(BillingSample.sample_ts)
                )
            ).all()
        )


async def test_sample_once_persists_clean_byte_delta_as_bps(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="delta",
        cidr_or_ip="203.0.113.70/32",
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(ts_ns=1_000_000_000, services=(service_counters(service.dp_id, 1_000),)),
                snapshot(ts_ns=4_000_000_000, services=(service_counters(service.dp_id, 10_000),)),
            ]
        ),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, 5, tzinfo=UTC),
    )

    await meter.sample_once()
    await meter.sample_once()

    rows = await samples_for(committed_db, service.id)

    assert [(row.clean_bps, row.window_seconds, row.is_reset) for row in rows] == [
        (3_000, 3, False)
    ]
    assert rows[0].dp_id == service.dp_id
    assert rows[0].sample_ts == datetime(2026, 7, 14, 12, 5, tzinfo=UTC)


async def test_sample_once_marks_negative_and_version_change_resets_without_negative_bps(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="reset",
        cidr_or_ip="203.0.113.71/32",
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(ts_ns=1_000_000_000, services=(service_counters(service.dp_id, 1_000),)),
                snapshot(ts_ns=6_000_000_000, services=(service_counters(service.dp_id, 200),)),
                snapshot(
                    ts_ns=11_000_000_000,
                    version=2,
                    services=(service_counters(service.dp_id, 1_200),),
                ),
            ]
        ),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, 10, tzinfo=UTC),
    )

    await meter.sample_once()
    await meter.sample_once()
    await meter.sample_once()

    rows = await samples_for(committed_db, service.id)

    assert [(row.clean_bps, row.is_reset) for row in rows] == [(40, True)]


async def test_sample_once_marks_an_active_version_change_as_a_reset(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="version-reset",
        cidr_or_ip="203.0.113.77/32",
    )
    sample_times = iter(
        [
            datetime(2026, 7, 14, 12, 10, tzinfo=UTC),
            datetime(2026, 7, 14, 12, 15, tzinfo=UTC),
        ]
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(ts_ns=1_000_000_000, services=(service_counters(service.dp_id, 1_000),)),
                snapshot(ts_ns=6_000_000_000, services=(service_counters(service.dp_id, 1_100),)),
                snapshot(
                    ts_ns=11_000_000_000,
                    version=2,
                    services=(service_counters(service.dp_id, 1_300),),
                ),
            ]
        ),
        session_factory=committed_db,
        now=sample_times.__next__,
    )

    await meter.sample_once()
    await meter.sample_once()
    await meter.sample_once()

    rows = await samples_for(committed_db, service.id)

    assert [(row.clean_bps, row.is_reset) for row in rows] == [(20, False), (260, True)]


async def test_sample_once_persists_zero_for_active_service_missing_a_counter(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    counted = await create_service(
        committed_db,
        name="counted",
        cidr_or_ip="203.0.113.72/32",
    )
    missing = await create_service(
        committed_db,
        name="missing-counter",
        cidr_or_ip="203.0.113.73/32",
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(
                    ts_ns=1_000_000_000,
                    services=(service_counters(counted.dp_id, 1_000),),
                ),
                snapshot(
                    ts_ns=6_000_000_000,
                    services=(service_counters(counted.dp_id, 1_500),),
                ),
            ]
        ),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, 15, tzinfo=UTC),
    )

    await meter.sample_once()
    await meter.sample_once()

    rows = await samples_for(committed_db, missing.id)

    assert [(row.clean_bps, row.window_seconds, row.is_reset) for row in rows] == [(0, 5, False)]


async def test_sample_once_seeds_the_first_snapshot_without_samples(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="baseline",
        cidr_or_ip="203.0.113.74/32",
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(ts_ns=1_000_000_000, services=(service_counters(service.dp_id, 1_000),)),
            ]
        ),
        session_factory=committed_db,
    )

    await meter.sample_once()

    assert await samples_for(committed_db, service.id) == []


async def test_sample_once_ignores_unknown_dp_ids(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="known",
        cidr_or_ip="203.0.113.75/32",
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(
                    ts_ns=1_000_000_000,
                    services=(
                        service_counters(service.dp_id, 1_000),
                        service_counters(9_999, 5_000),
                    ),
                ),
                snapshot(
                    ts_ns=6_000_000_000,
                    services=(
                        service_counters(service.dp_id, 1_500),
                        service_counters(9_999, 6_000),
                    ),
                ),
            ]
        ),
        session_factory=committed_db,
    )

    await meter.sample_once()
    await meter.sample_once()

    rows = await samples_for(committed_db, service.id)

    assert [(row.dp_id, row.clean_bps) for row in rows] == [(service.dp_id, 100)]


async def test_sample_once_is_idempotent_for_a_repeated_aligned_tick(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="idempotent",
        cidr_or_ip="203.0.113.76/32",
    )
    aligned_tick = datetime(2026, 7, 14, 12, 20, tzinfo=UTC)
    meter = BillingMeter(
        reader=FakeTelemetryReader(
            snapshots=[
                snapshot(ts_ns=1_000_000_000, services=(service_counters(service.dp_id, 1_000),)),
                snapshot(ts_ns=6_000_000_000, services=(service_counters(service.dp_id, 1_500),)),
                snapshot(ts_ns=11_000_000_000, services=(service_counters(service.dp_id, 2_000),)),
            ]
        ),
        session_factory=committed_db,
        now=lambda: aligned_tick,
    )

    await meter.sample_once()
    await meter.sample_once()
    await meter.sample_once()

    rows = await samples_for(committed_db, service.id)

    assert [(row.sample_ts, row.clean_bps) for row in rows] == [(aligned_tick, 100)]
