import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AgentJob,
    Alert,
    AlertNotification,
    AlertScope,
    AlertSeverity,
    AlertState,
    ApplyStatus,
    BillingSample,
    BillingStatus,
    BillingUsage,
    ChangeTrigger,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    NodeHealthSnapshot,
    OveragePolicy,
    ProtectedService,
    Tenant,
    XdpMode,
)
from app.db.session import session_scope
from app.services.apply import enqueue_service_update
from app.services.feeds import create_source, enqueue_sync
from app.worker.alert_evaluator import AlertEvaluator
from app.worker.applier import GlobalDenyApplyResult, ServiceConfig
from app.worker.billing import BillingMeter
from app.worker.feed_runner import FeedRunner
from app.worker.telemetry import TelemetryAggregator
from app.worker.telemetry_reader import FakeTelemetryReader, NodeCounters, TelemetrySnapshot
from app.worker.worker import Worker

pytestmark = pytest.mark.integration


class RecordingApplier:
    def __init__(self) -> None:
        self.applied = asyncio.Event()

    async def apply(self, config: ServiceConfig) -> None:
        del config
        self.applied.set()


class BarrierApplier:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def apply(self, config: ServiceConfig) -> None:
        del config
        self.entered.set()
        await self.release.wait()


class NoopGlobalApplier:
    async def apply_global(self, snapshot: object) -> GlobalDenyApplyResult:
        del snapshot
        return GlobalDenyApplyResult(active_slot=0, node_map_version=0)


def runtime_settings(**values: object) -> Settings:
    return Settings(
        worker_poll_timeout_seconds=0.05,
        worker_reconcile_interval_seconds=0.05,
        worker_backoff_initial_seconds=0.01,
        worker_backoff_max_seconds=0.05,
        worker_shutdown_grace_seconds=0.05,
        **values,
    )


async def enqueue_job(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as db:
        tenant = Tenant(name=f"{name}-tenant")
        service = ProtectedService(
            tenant=tenant,
            name=name,
            cidr_or_ip="203.0.113.60/32",
            apply_status=ApplyStatus.pending,
            version=1,
        )
        db.add_all([tenant, service])
        await db.flush()
        job = await enqueue_service_update(db, service, actor=None, trigger=ChangeTrigger.service)
    return service.id, job.id


async def get_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
) -> AgentJob:
    async with session_factory() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        return job


async def wait_for_job_status(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    expected_status: JobStatus,
) -> None:
    for _ in range(500):
        if (await get_job(session_factory, job_id)).status == expected_status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {expected_status}")


async def get_service(
    session_factory: async_sessionmaker[AsyncSession],
    service_id: uuid.UUID,
) -> ProtectedService:
    async with session_factory() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        return service


async def enqueue_feed_job(name: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with session_scope() as db:
        source = await create_source(
            db,
            {
                "name": name,
                "url": f"https://feeds.example.test/{name}.txt",
                "sync_interval_seconds": 300,
            },
            actor=None,
        )
        run = await enqueue_sync(
            db,
            source,
            trigger=ChangeTrigger.feed_manual,
            dry_run=False,
            actor=None,
        )
        job = await db.scalar(select(AgentJob).where(AgentJob.feed_sync_run_id == run.id))
        assert job is not None
    return source.id, run.id, job.id


async def get_feed_records(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[FeedSyncRun, AgentJob]:
    async with session_factory() as db:
        run = await db.get(FeedSyncRun, run_id)
        job = await db.get(AgentJob, job_id)
        assert run is not None
        assert job is not None
        return run, job


async def test_worker_brpop_processes_dispatched_job_and_exits_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    applier = RecordingApplier()
    stop = asyncio.Event()
    waiting_for_pop = asyncio.Event()
    original_wait_for_pop = Worker._wait_for_pop

    async def observe_wait_for_pop(self: Worker, current_stop: asyncio.Event) -> uuid.UUID | None:
        waiting_for_pop.set()
        return await original_wait_for_pop(self, current_stop)

    monkeypatch.setattr(Worker, "_wait_for_pop", observe_wait_for_pop)
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        applier=applier,
    )
    started = time.monotonic()
    task = asyncio.create_task(worker.run(stop))

    await asyncio.wait_for(waiting_for_pop.wait(), timeout=2)
    service_id, job_id = await enqueue_job("runtime-brpop")
    await asyncio.wait_for(applier.applied.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    service = await get_service(committed_db, service_id)
    job = await get_job(committed_db, job_id)
    assert time.monotonic() - started <= 5
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded


async def test_worker_processes_service_while_feed_fetch_is_blocked(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    fetch_started = asyncio.Event()
    keep_fetch_blocked = asyncio.Event()

    async def barrier_fetch(request: httpx.Request) -> httpx.Response:
        del request
        fetch_started.set()
        await keep_fetch_blocked.wait()
        return httpx.Response(200, content=b"198.51.100.70\n")

    _, first_run_id, first_job_id = await enqueue_feed_job("runtime-barrier-first")
    _, queued_run_id, queued_job_id = await enqueue_feed_job("runtime-barrier-queued")
    feed_runner = FeedRunner(
        client=httpx.AsyncClient(transport=httpx.MockTransport(barrier_fetch)),
        settings=runtime_settings(),
        global_applier=NoopGlobalApplier(),
    )
    service_applier = RecordingApplier()
    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        applier=service_applier,
        feed_runner=feed_runner,
    )
    worker_task = asyncio.create_task(worker.run(stop))

    try:
        await asyncio.wait_for(fetch_started.wait(), timeout=2)
        started = time.monotonic()
        service_id, service_job_id = await enqueue_job("runtime-feed-isolation")
        await asyncio.wait_for(service_applier.applied.wait(), timeout=5)
        await asyncio.wait_for(
            wait_for_job_status(committed_db, service_job_id, JobStatus.succeeded),
            timeout=5,
        )

        service = await get_service(committed_db, service_id)
        service_job = await get_job(committed_db, service_job_id)
        first_run, first_job = await get_feed_records(committed_db, first_run_id, first_job_id)
        queued_run, queued_job = await get_feed_records(
            committed_db,
            queued_run_id,
            queued_job_id,
        )

        assert time.monotonic() - started <= 5
        assert (service.apply_status, service.active_version, service_job.status) == (
            ApplyStatus.active,
            1,
            JobStatus.succeeded,
        )
        assert (first_run.status, first_job.status) == (
            FeedSyncStatus.running,
            JobStatus.applying,
        )
        assert (queued_run.status, queued_job.status) == (
            FeedSyncStatus.queued,
            JobStatus.queued,
        )
    finally:
        stop.set()
        await asyncio.wait_for(worker_task, timeout=2)


async def test_worker_shutdown_timeout_leaves_applying_job_for_startup_recovery(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    stop = asyncio.Event()
    blocking_applier = BarrierApplier()
    waiting_for_pop = asyncio.Event()
    original_wait_for_pop = Worker._wait_for_pop

    async def observe_wait_for_pop(self: Worker, current_stop: asyncio.Event) -> uuid.UUID | None:
        waiting_for_pop.set()
        return await original_wait_for_pop(self, current_stop)

    monkeypatch.setattr(Worker, "_wait_for_pop", observe_wait_for_pop)
    first_worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        applier=blocking_applier,
    )
    first_run = asyncio.create_task(first_worker.run(stop))

    await asyncio.wait_for(waiting_for_pop.wait(), timeout=2)
    service_id, job_id = await enqueue_job("runtime-shutdown")
    await asyncio.wait_for(blocking_applier.entered.wait(), timeout=2)
    stop.set()
    await asyncio.wait_for(first_run, timeout=2)

    assert (await get_service(committed_db, service_id)).apply_status == ApplyStatus.applying
    assert (await get_job(committed_db, job_id)).status == JobStatus.applying

    recovery_applier = RecordingApplier()
    recovery_stop = asyncio.Event()
    recovery_worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        applier=recovery_applier,
    )
    recovery_run = asyncio.create_task(recovery_worker.run(recovery_stop))
    await asyncio.wait_for(recovery_applier.applied.wait(), timeout=2)
    recovery_stop.set()
    await asyncio.wait_for(recovery_run, timeout=2)

    service = await get_service(committed_db, service_id)
    job = await get_job(committed_db, job_id)
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded


async def test_worker_logs_effective_configuration_at_startup(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del committed_db
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    monkeypatch.setattr(logging.getLogger("app.worker.worker"), "disabled", False)
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    stop.set()
    worker = Worker(settings=runtime_settings(), redis=redis_client)

    await worker.run(stop)

    assert "Worker starting" in caplog.text
    assert caplog.records[-1].queue_key == "apply:jobs"


async def test_worker_runs_and_stops_the_telemetry_lane(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    snapshot = TelemetrySnapshot(
        ts_ns=1_000_000_000,
        active_slot=0,
        active_version=1,
        xdp_mode="native",
        xdp_prog_id=1,
        xdp_ifindex=2,
        node=NodeCounters(
            counters={"map_error": 0},
            sample_stats={},
            bloom_stats={},
        ),
        services=(),
    )
    telemetry = TelemetryAggregator(
        reader=FakeTelemetryReader(snapshots=[snapshot]),
        session_factory=committed_db,
        interval_seconds=1,
        retention_seconds=60,
        node_clean_capacity_gbps=Decimal("40"),
    )
    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(worker_telemetry_interval_seconds=1),
        redis=redis_client,
        session_factory=committed_db,
        telemetry=telemetry,
    )
    task = asyncio.create_task(worker.run(stop))

    for _ in range(100):
        async with committed_db() as db:
            health = await db.scalar(select(NodeHealthSnapshot))
        if health is not None:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("Telemetry lane did not persist a health snapshot")

    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert health.xdp_mode is XdpMode.native


async def test_telemetry_lane_failure_does_not_stop_job_processing(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    started = asyncio.Event()

    class FailingTelemetry:
        async def run_loop(self, _stop: asyncio.Event) -> None:
            started.set()
            raise RuntimeError("telemetry lane failed")

    applier = RecordingApplier()
    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        applier=applier,
        telemetry=FailingTelemetry(),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(worker.run(stop))

    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        _, job_id = await enqueue_job("runtime-telemetry-isolation")
        await asyncio.wait_for(applier.applied.wait(), timeout=2)
        await asyncio.wait_for(
            wait_for_job_status(committed_db, job_id, JobStatus.succeeded),
            timeout=2,
        )
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)


async def test_worker_stops_the_alert_lane_and_alert_evaluator_prunes_only_expired_history(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    started = asyncio.Event()

    class BlockingAlerts:
        async def run_loop(self, stop: asyncio.Event) -> None:
            started.set()
            await stop.wait()

    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        alerts=BlockingAlerts(),
    )
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(started.wait(), timeout=2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    class UnusedSources:
        async def load(self, db: AsyncSession, now: datetime) -> object:
            del db, now
            raise AssertionError("prune does not load sources")

    class UnusedDispatcher:
        async def enqueue(self, db: AsyncSession, alert: Alert, trigger: str) -> None:
            del db, alert, trigger

        async def dispatch_pending(self, db: AsyncSession) -> None:
            del db

    now = datetime(2026, 7, 14, tzinfo=UTC)
    async with committed_db() as db:
        await db.execute(delete(AlertNotification))
        await db.execute(delete(Alert))
        old = Alert(
            rule_key="old",
            scope=AlertScope.node,
            scope_key="old",
            severity=AlertSeverity.warning,
            state=AlertState.resolved,
            context={},
            fire_streak=1,
            clear_streak=1,
            first_observed_at=now,
            resolved_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        recent = Alert(
            rule_key="recent",
            scope=AlertScope.node,
            scope_key="recent",
            severity=AlertSeverity.warning,
            state=AlertState.resolved,
            context={},
            fire_streak=1,
            clear_streak=1,
            first_observed_at=now,
            resolved_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        firing = Alert(
            rule_key="firing",
            scope=AlertScope.node,
            scope_key="firing",
            severity=AlertSeverity.warning,
            state=AlertState.firing,
            context={},
            fire_streak=1,
            clear_streak=0,
            first_observed_at=now,
        )
        db.add_all([old, recent, firing])
        await db.flush()
        evaluator = AlertEvaluator(
            sources=UnusedSources(),  # type: ignore[arg-type]
            dispatcher=UnusedDispatcher(),  # type: ignore[arg-type]
            session_factory=committed_db,
            fire_ticks=1,
            clear_ticks=1,
            renotify_seconds=1,
            interval_seconds=1,
            history_retention_days=90,
        )
        await evaluator.prune_history(db, now)
        await db.commit()

    async with committed_db() as db:
        remaining = set((await db.scalars(select(Alert.rule_key))).all())
    assert remaining == {"recent", "firing"}


async def test_billing_meter_prunes_only_samples_for_old_finalized_periods(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    old_finalized_service = ProtectedService(
        tenant=Tenant(name="billing-prune-old-tenant"),
        name="billing-prune-old",
        cidr_or_ip="203.0.113.240/32",
    )
    recent_finalized_service = ProtectedService(
        tenant=Tenant(name="billing-prune-recent-tenant"),
        name="billing-prune-recent",
        cidr_or_ip="203.0.113.241/32",
    )
    old_open_service = ProtectedService(
        tenant=Tenant(name="billing-prune-open-tenant"),
        name="billing-prune-open",
        cidr_or_ip="203.0.113.242/32",
    )
    now = datetime(2026, 7, 14, tzinfo=UTC)

    async with committed_db() as db:
        db.add_all([old_finalized_service, recent_finalized_service, old_open_service])
        await db.flush()
        db.add_all(
            [
                BillingUsage(
                    service_id=old_finalized_service.id,
                    tenant_id=old_finalized_service.tenant_id,
                    service_name=old_finalized_service.name,
                    period_start=datetime(2025, 5, 1, tzinfo=UTC),
                    period_end=datetime(2025, 6, 1, tzinfo=UTC),
                    billing_metric="p95_clean_bps",
                    committed_clean_gbps=Decimal("1.00"),
                    p95_clean_gbps=Decimal("1.00"),
                    billed_gbps=Decimal("1.00"),
                    overage_gbps=Decimal("0.00"),
                    overage_policy=OveragePolicy.billed,
                    sample_count=1,
                    status=BillingStatus.final,
                    finalized_at=datetime(2025, 6, 1, tzinfo=UTC),
                ),
                BillingUsage(
                    service_id=recent_finalized_service.id,
                    tenant_id=recent_finalized_service.tenant_id,
                    service_name=recent_finalized_service.name,
                    period_start=datetime(2026, 6, 1, tzinfo=UTC),
                    period_end=datetime(2026, 7, 1, tzinfo=UTC),
                    billing_metric="p95_clean_bps",
                    committed_clean_gbps=Decimal("1.00"),
                    p95_clean_gbps=Decimal("1.00"),
                    billed_gbps=Decimal("1.00"),
                    overage_gbps=Decimal("0.00"),
                    overage_policy=OveragePolicy.billed,
                    sample_count=1,
                    status=BillingStatus.final,
                    finalized_at=datetime(2026, 7, 1, tzinfo=UTC),
                ),
                BillingUsage(
                    service_id=old_open_service.id,
                    tenant_id=old_open_service.tenant_id,
                    service_name=old_open_service.name,
                    period_start=datetime(2025, 4, 1, tzinfo=UTC),
                    period_end=datetime(2025, 5, 1, tzinfo=UTC),
                    billing_metric="p95_clean_bps",
                    committed_clean_gbps=Decimal("1.00"),
                    p95_clean_gbps=Decimal("1.00"),
                    billed_gbps=Decimal("1.00"),
                    overage_gbps=Decimal("0.00"),
                    overage_policy=OveragePolicy.billed,
                    sample_count=1,
                    status=BillingStatus.open,
                ),
                BillingSample(
                    service_id=old_finalized_service.id,
                    dp_id=old_finalized_service.dp_id,
                    sample_ts=datetime(2025, 5, 15, tzinfo=UTC),
                    clean_bps=10,
                    window_seconds=300,
                    is_reset=False,
                ),
                BillingSample(
                    service_id=recent_finalized_service.id,
                    dp_id=recent_finalized_service.dp_id,
                    sample_ts=datetime(2026, 6, 15, tzinfo=UTC),
                    clean_bps=20,
                    window_seconds=300,
                    is_reset=False,
                ),
                BillingSample(
                    service_id=old_open_service.id,
                    dp_id=old_open_service.dp_id,
                    sample_ts=datetime(2025, 4, 15, tzinfo=UTC),
                    clean_bps=30,
                    window_seconds=300,
                    is_reset=False,
                ),
            ]
        )
        await db.commit()

    try:
        meter = BillingMeter(
            reader=FakeTelemetryReader(snapshots=[]),
            session_factory=committed_db,
            now=lambda: now,
            sample_retention_days=400,
        )

        await meter.prune_samples()

        async with committed_db() as db:
            retained_service_ids = set((await db.scalars(select(BillingSample.service_id))).all())

        assert retained_service_ids == {recent_finalized_service.id, old_open_service.id}
    finally:
        async with committed_db() as db:
            await db.execute(
                delete(BillingUsage).where(
                    BillingUsage.service_id.in_(
                        [
                            old_finalized_service.id,
                            recent_finalized_service.id,
                            old_open_service.id,
                        ]
                    )
                )
            )
            await db.commit()


async def test_billing_meter_run_loop_recovers_after_a_failed_tick(
    committed_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meter = BillingMeter(
        reader=FakeTelemetryReader(snapshots=[]),
        session_factory=committed_db,
        interval_seconds=0.01,
    )
    calls = 0
    continued = asyncio.Event()

    async def fail_once_then_continue() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("billing tick failed")
        continued.set()

    monkeypatch.setattr(meter, "tick", fail_once_then_continue)
    stop = asyncio.Event()
    task = asyncio.create_task(meter.run_loop(stop))

    try:
        await asyncio.wait_for(continued.wait(), timeout=2)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    assert calls >= 2


async def test_worker_cancels_the_billing_lane_on_stop(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingBilling:
        async def run_loop(self, _stop: asyncio.Event) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        billing=BlockingBilling(),
    )
    task = asyncio.create_task(worker.run(stop))

    await asyncio.wait_for(started.wait(), timeout=2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert cancelled.is_set()


async def test_worker_runtime_spawns_and_stops_nexthop_lane(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)
    started = asyncio.Event()
    stopped = asyncio.Event()

    class RecordingNextHop:
        def request_resolve(self, dp_id: int, ip: str) -> None:
            pass

        def request_evict(self, dp_id: int) -> None:
            pass

        async def run_loop(self, stop: asyncio.Event) -> None:
            started.set()
            await stop.wait()
            stopped.set()

    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        nexthop=RecordingNextHop(),
    )
    task = asyncio.create_task(worker.run(stop))

    await asyncio.wait_for(started.wait(), timeout=2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert stopped.is_set()


async def _noop() -> None:
    return None
