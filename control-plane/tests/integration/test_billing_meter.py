from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BillingSample,
    BillingStatus,
    BillingUsage,
    OveragePolicy,
    ProtectedService,
    ServicePlan,
    Tenant,
)
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


async def create_plan(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    service: ProtectedService,
    committed_clean_gbps: Decimal,
    ceiling_clean_gbps: Decimal,
    overage_policy: OveragePolicy = OveragePolicy.billed,
) -> None:
    async with session_factory() as db:
        db.add(
            ServicePlan(
                service_id=service.id,
                committed_clean_gbps=committed_clean_gbps,
                ceiling_clean_gbps=ceiling_clean_gbps,
                overage_policy=overage_policy,
            )
        )
        await db.commit()


async def add_samples(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    service: ProtectedService,
    period_start: datetime,
    clean_bps: list[int],
) -> None:
    async with session_factory() as db:
        db.add_all(
            [
                BillingSample(
                    service_id=service.id,
                    dp_id=service.dp_id,
                    sample_ts=period_start + timedelta(minutes=index),
                    clean_bps=value,
                    window_seconds=300,
                    is_reset=False,
                )
                for index, value in enumerate(clean_bps)
            ]
        )
        await db.commit()


async def usages_for(
    session_factory: async_sessionmaker[AsyncSession],
    service_id: object,
) -> list[BillingUsage]:
    async with session_factory() as db:
        return list(
            (
                await db.scalars(
                    select(BillingUsage)
                    .where(BillingUsage.service_id == service_id)
                    .order_by(BillingUsage.period_start)
                )
            ).all()
        )


async def all_usages(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[BillingUsage]:
    async with session_factory() as db:
        return list(
            (await db.scalars(select(BillingUsage).order_by(BillingUsage.period_start))).all()
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


async def test_refresh_open_periods_calculates_p95_billed_and_overage(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="p95-rollup",
        cidr_or_ip="203.0.113.78/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("2.00"),
        ceiling_clean_gbps=Decimal("10.00"),
    )
    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    await add_samples(
        committed_db,
        service=service,
        period_start=period_start,
        clean_bps=[125_000_000] * 18 + [1_000_000_000] * 2,
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, tzinfo=UTC),
    )

    await meter.refresh_open_periods()

    rows = await usages_for(committed_db, service.id)

    assert len(rows) == 1
    assert rows[0].period_start == period_start
    assert rows[0].period_end == datetime(2026, 8, 1, tzinfo=UTC)
    assert rows[0].status == BillingStatus.open
    assert rows[0].billing_metric == "p95_clean_bps"
    assert rows[0].committed_clean_gbps == Decimal("2.00")
    assert rows[0].p95_clean_gbps == Decimal("8.00")
    assert rows[0].billed_gbps == Decimal("8.00")
    assert rows[0].overage_gbps == Decimal("6.00")
    assert rows[0].overage_policy == OveragePolicy.billed
    assert rows[0].sample_count == 20


async def test_refresh_open_periods_updates_the_existing_open_estimate(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="open-estimate",
        cidr_or_ip="203.0.113.79/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("1.00"),
        ceiling_clean_gbps=Decimal("10.00"),
    )
    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    await add_samples(
        committed_db,
        service=service,
        period_start=period_start,
        clean_bps=[125_000_000],
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, tzinfo=UTC),
    )

    await meter.refresh_open_periods()
    await add_samples(
        committed_db,
        service=service,
        period_start=period_start + timedelta(minutes=1),
        clean_bps=[250_000_000],
    )
    await meter.refresh_open_periods()

    rows = await usages_for(committed_db, service.id)

    assert len(rows) == 1
    assert rows[0].sample_count == 2
    assert rows[0].p95_clean_gbps == Decimal("2.00")
    assert rows[0].billed_gbps == Decimal("2.00")
    assert rows[0].overage_gbps == Decimal("1.00")


async def test_refresh_open_periods_stores_overage_for_capped_policy(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="capped-overage",
        cidr_or_ip="203.0.113.80/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("1.00"),
        ceiling_clean_gbps=Decimal("10.00"),
        overage_policy=OveragePolicy.capped,
    )
    await add_samples(
        committed_db,
        service=service,
        period_start=datetime(2026, 7, 1, tzinfo=UTC),
        clean_bps=[250_000_000],
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, tzinfo=UTC),
    )

    await meter.refresh_open_periods()

    row = (await usages_for(committed_db, service.id))[0]

    assert row.overage_policy == OveragePolicy.capped
    assert row.billed_gbps == Decimal("2.00")
    assert row.overage_gbps == Decimal("1.00")


async def test_refresh_open_periods_uses_the_committed_floor_without_samples(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="zero-samples",
        cidr_or_ip="203.0.113.81/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("3.00"),
        ceiling_clean_gbps=Decimal("3.00"),
    )
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: datetime(2026, 7, 14, 12, tzinfo=UTC),
    )

    await meter.refresh_open_periods()

    row = (await usages_for(committed_db, service.id))[0]

    assert row.p95_clean_gbps == Decimal("0.00")
    assert row.billed_gbps == Decimal("3.00")
    assert row.overage_gbps == Decimal("0.00")
    assert row.sample_count == 0


async def test_finalize_due_periods_finalizes_at_the_boundary_and_leaves_final_rows_immutable(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="month-boundary",
        cidr_or_ip="203.0.113.82/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("1.00"),
        ceiling_clean_gbps=Decimal("10.00"),
    )
    await add_samples(
        committed_db,
        service=service,
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        clean_bps=[250_000_000],
    )
    clock = [datetime(2026, 6, 30, 23, 59, tzinfo=UTC)]
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: clock[0],
    )

    await meter.refresh_open_periods()
    clock[0] = datetime(2026, 7, 1, tzinfo=UTC)
    await meter.finalize_due_periods()
    finalized = (await usages_for(committed_db, service.id))[0]
    original_values = (
        finalized.committed_clean_gbps,
        finalized.p95_clean_gbps,
        finalized.billed_gbps,
        finalized.overage_gbps,
        finalized.finalized_at,
    )
    await meter.refresh_open_periods()
    clock[0] = datetime(2026, 7, 2, tzinfo=UTC)
    await meter.finalize_due_periods()

    rows = await usages_for(committed_db, service.id)
    june_row = next(row for row in rows if row.period_start.month == 6)

    assert len([row for row in rows if row.period_start.month == 6]) == 1
    assert june_row.status == BillingStatus.final
    assert june_row.finalized_at == datetime(2026, 7, 1, tzinfo=UTC)
    assert (
        june_row.committed_clean_gbps,
        june_row.p95_clean_gbps,
        june_row.billed_gbps,
        june_row.overage_gbps,
        june_row.finalized_at,
    ) == original_values


async def test_finalize_due_periods_finalizes_an_orphaned_usage_after_service_deletion(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="deleted-service",
        cidr_or_ip="203.0.113.83/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("1.00"),
        ceiling_clean_gbps=Decimal("10.00"),
    )
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: now,
    )

    await meter.refresh_open_periods()
    async with committed_db() as db:
        persisted_service = await db.get(ProtectedService, service.id)
        assert persisted_service is not None
        await db.delete(persisted_service)
        await db.commit()
    await meter.finalize_due_periods()

    rows = await all_usages(committed_db)

    assert len(rows) == 1
    assert rows[0].service_id is None
    assert rows[0].service_name == "deleted-service"
    assert rows[0].status == BillingStatus.final
    assert rows[0].finalized_at == now


async def test_finalize_due_periods_is_idempotent_when_re_run(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="idempotent-finalize",
        cidr_or_ip="203.0.113.84/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("1.00"),
        ceiling_clean_gbps=Decimal("10.00"),
    )
    clock = [datetime(2026, 6, 30, 23, 59, tzinfo=UTC)]
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: clock[0],
    )

    await meter.refresh_open_periods()
    clock[0] = datetime(2026, 7, 1, tzinfo=UTC)
    await meter.finalize_due_periods()
    await meter.finalize_due_periods()

    rows = await usages_for(committed_db, service.id)

    assert len(rows) == 1
    assert rows[0].status == BillingStatus.final
    assert rows[0].finalized_at == datetime(2026, 7, 1, tzinfo=UTC)


async def test_refresh_open_periods_snapshots_committed_value_effective_at_close(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service = await create_service(
        committed_db,
        name="committed-snapshot",
        cidr_or_ip="203.0.113.85/32",
    )
    await create_plan(
        committed_db,
        service=service,
        committed_clean_gbps=Decimal("1.00"),
        ceiling_clean_gbps=Decimal("10.00"),
    )
    await add_samples(
        committed_db,
        service=service,
        period_start=datetime(2026, 6, 1, tzinfo=UTC),
        clean_bps=[250_000_000],
    )
    clock = [datetime(2026, 6, 30, 23, 59, tzinfo=UTC)]
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        now=lambda: clock[0],
    )

    await meter.refresh_open_periods()
    async with committed_db() as db:
        plan = await db.scalar(select(ServicePlan).where(ServicePlan.service_id == service.id))
        assert plan is not None
        plan.committed_clean_gbps = Decimal("3.00")
        await db.commit()
    await meter.refresh_open_periods()
    clock[0] = datetime(2026, 7, 1, tzinfo=UTC)
    await meter.finalize_due_periods()

    row = (await usages_for(committed_db, service.id))[0]

    assert row.status == BillingStatus.final
    assert row.committed_clean_gbps == Decimal("3.00")
    assert row.billed_gbps == Decimal("3.00")
    assert row.overage_gbps == Decimal("0.00")
