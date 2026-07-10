import asyncio
import uuid
from datetime import timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AgentJob,
    ChangeTrigger,
    FeedSyncRun,
    FeedSyncStatus,
    GlobalDenyState,
    JobStatus,
    JobType,
    ThreatFeedSource,
    utc_now,
)
from app.db.session import session_scope
from app.services.feeds import create_source, enqueue_sync
from app.worker.feed_jobs import JOB_LIFECYCLES
from app.worker.feed_scheduler import enqueue_due_feed_syncs
from app.worker.worker import Worker

pytestmark = pytest.mark.integration


async def seed_source(
    name: str,
    *,
    enabled: bool = True,
    next_sync_at: object | None = None,
) -> uuid.UUID:
    async with session_scope() as db:
        source = await create_source(
            db,
            {
                "name": name,
                "url": f"https://feeds.example.test/{name}",
                "sync_interval_seconds": 300,
                "enabled": enabled,
            },
            actor=None,
        )
        if next_sync_at is not None:
            source.next_sync_at = next_sync_at
            await db.flush()
    return source.id


async def feed_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[AgentJob]:
    async with session_factory() as db:
        return list(
            (
                await db.scalars(
                    select(AgentJob)
                    .where(AgentJob.job_type == JobType.feed_sync)
                    .order_by(AgentJob.created_at, AgentJob.id)
                )
            ).all()
        )


async def source_and_run(
    session_factory: async_sessionmaker[AsyncSession],
    source_id: uuid.UUID,
    run_id: uuid.UUID,
) -> tuple[ThreatFeedSource, FeedSyncRun]:
    async with session_factory() as db:
        source = await db.get(ThreatFeedSource, source_id)
        run = await db.get(FeedSyncRun, run_id)
        assert source is not None
        assert run is not None
        return source, run


async def test_tick_enqueues_each_due_enabled_source_once_and_skips_ineligible_sources(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    first_due_id = await seed_source("scheduler-first-due", next_sync_at=now - timedelta(seconds=1))
    second_due_id = await seed_source("scheduler-second-due", next_sync_at=now)
    await seed_source("scheduler-not-due", next_sync_at=now + timedelta(seconds=1))
    await seed_source("scheduler-disabled", enabled=False)

    count = await enqueue_due_feed_syncs(committed_db, now)

    jobs = await feed_jobs(committed_db)
    assert count == 2
    assert {job.trigger for job in jobs} == {ChangeTrigger.feed_schedule}
    assert {job.feed_sync_run_id for job in jobs} == {
        run.id for run in await _runs_for_sources(committed_db, {first_due_id, second_due_id})
    }


async def test_disabled_source_skips_schedule_but_manual_sync_remains_independent(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    source_id = await seed_source("scheduler-disabled-manual", enabled=False)

    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        manual_run = await enqueue_sync(
            db,
            source,
            trigger=ChangeTrigger.feed_manual,
            dry_run=False,
            actor=None,
        )

    assert await enqueue_due_feed_syncs(committed_db, now) == 0
    jobs = await feed_jobs(committed_db)
    assert len(jobs) == 1
    assert jobs[0].feed_sync_run_id == manual_run.id
    assert jobs[0].trigger == ChangeTrigger.feed_manual


async def test_concurrent_ticks_and_manual_sync_share_one_inflight_source_run(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    source_id = await seed_source("scheduler-concurrent", next_sync_at=now - timedelta(seconds=1))

    counts = await asyncio.gather(
        enqueue_due_feed_syncs(committed_db, now),
        enqueue_due_feed_syncs(committed_db, now),
    )
    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        manual_run = await enqueue_sync(
            db,
            source,
            trigger=ChangeTrigger.feed_manual,
            dry_run=False,
            actor=None,
        )

    jobs = await feed_jobs(committed_db)
    assert sum(counts) == 1
    assert len(jobs) == 1
    assert jobs[0].feed_sync_run_id == manual_run.id
    assert jobs[0].status == JobStatus.queued


async def test_persisted_due_source_is_enqueued_immediately_after_worker_restart(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    source_id = await seed_source("scheduler-restart", next_sync_at=now - timedelta(seconds=1))

    assert await enqueue_due_feed_syncs(committed_db, now) == 1

    runs = await _runs_for_sources(committed_db, {source_id})
    assert len(runs) == 1
    assert runs[0].trigger == ChangeTrigger.feed_schedule


@pytest.mark.parametrize("outcome", [FeedSyncStatus.success, FeedSyncStatus.partial])
async def test_terminal_success_outcomes_persist_the_next_due_time(
    committed_db: async_sessionmaker[AsyncSession],
    outcome: FeedSyncStatus,
) -> None:
    source_id = await seed_source("scheduler-terminal-" + outcome.value)
    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        run = await enqueue_sync(
            db,
            source,
            trigger=ChangeTrigger.feed_manual,
            dry_run=False,
            actor=None,
        )
        job = (await db.scalars(select(AgentJob).where(AgentJob.feed_sync_run_id == run.id))).one()
        assert await JOB_LIFECYCLES[JobType.feed_sync].claim(db, job)
        await JOB_LIFECYCLES[JobType.feed_sync].succeed(db, job, outcome)

    source, run = await source_and_run(committed_db, source_id, run.id)
    assert run.finished_at is not None
    assert source.next_sync_at == run.finished_at + timedelta(seconds=source.sync_interval_seconds)


async def test_terminal_failure_persists_the_next_due_time(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id = await seed_source("scheduler-terminal-failure")
    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        run = await enqueue_sync(
            db,
            source,
            trigger=ChangeTrigger.feed_manual,
            dry_run=False,
            actor=None,
        )
        job = (await db.scalars(select(AgentJob).where(AgentJob.feed_sync_run_id == run.id))).one()
        assert await JOB_LIFECYCLES[JobType.feed_sync].claim(db, job)
        await JOB_LIFECYCLES[JobType.feed_sync].fail(db, job, "upstream failed")

    source, run = await source_and_run(committed_db, source_id, run.id)
    assert run.finished_at is not None
    assert run.status == FeedSyncStatus.failed
    assert source.next_sync_at == run.finished_at + timedelta(seconds=source.sync_interval_seconds)


async def test_tick_enqueues_and_deduplicates_pending_global_convergence(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    async with session_scope() as db:
        db.add(
            GlobalDenyState(
                id=1,
                desired_revision=7,
                active_revision=6,
                desired_digest="a" * 64,
                active_digest="b" * 64,
            )
        )

    assert await enqueue_due_feed_syncs(committed_db, now) == 1
    assert await enqueue_due_feed_syncs(committed_db, now) == 0

    async with committed_db() as db:
        jobs = list(
            (
                await db.scalars(
                    select(AgentJob).where(AgentJob.job_type == JobType.global_deny_apply)
                )
            ).all()
        )
    assert len(jobs) == 1
    assert (jobs[0].target_type, jobs[0].target_id, jobs[0].feed_sync_run_id) == (
        "global_deny",
        None,
        None,
    )
    assert jobs[0].version == 7
    assert jobs[0].trigger == ChangeTrigger.global_deny_retry
    assert jobs[0].status == JobStatus.queued


async def test_tick_requeues_terminal_global_convergence_for_the_same_revision(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    async with session_scope() as db:
        db.add(
            GlobalDenyState(
                id=1,
                desired_revision=8,
                active_revision=7,
                desired_digest="c" * 64,
                active_digest="d" * 64,
            )
        )

    assert await enqueue_due_feed_syncs(committed_db, now) == 1
    async with session_scope() as db:
        job = (
            await db.scalars(select(AgentJob).where(AgentJob.job_type == JobType.global_deny_apply))
        ).one()
        job.status = JobStatus.failed
        job.finished_at = now
        await db.flush()
        job_id = job.id

    assert await enqueue_due_feed_syncs(committed_db, now) == 1
    async with committed_db() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
    assert job.status == JobStatus.queued
    assert job.finished_at is None
    assert job.error is None


async def test_scheduler_database_failure_backs_off_without_skipping_startup_reconciliation(
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler_attempted = asyncio.Event()
    backed_off = asyncio.Event()
    reconciled = asyncio.Event()
    stop = asyncio.Event()

    async def fail_scheduler(*args: object, **kwargs: object) -> int:
        del args, kwargs
        scheduler_attempted.set()
        raise OperationalError("scheduler", {}, RuntimeError("database unavailable"))

    async def observe_backoff(
        self: Worker,
        current_stop: asyncio.Event,
        current: float | None,
        operation: str,
    ) -> float:
        del self, current_stop, current
        assert operation == "startup feed scheduling"
        backed_off.set()
        return 0.01

    async def reconcile_after_scheduler_failure(**kwargs: object) -> int:
        del kwargs
        reconciled.set()
        stop.set()
        return 0

    monkeypatch.setattr("app.worker.worker.enqueue_due_feed_syncs", fail_scheduler)
    monkeypatch.setattr(Worker, "_back_off", observe_backoff)
    monkeypatch.setattr("app.worker.worker.reconcile_once", reconcile_after_scheduler_failure)
    monkeypatch.setattr("app.worker.worker.close_redis_client", _noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", _noop)

    worker = Worker(settings=Settings(), redis=redis_client)
    await asyncio.wait_for(worker.run(stop), timeout=2)

    assert scheduler_attempted.is_set()
    assert backed_off.is_set()
    assert reconciled.is_set()


async def _runs_for_sources(
    session_factory: async_sessionmaker[AsyncSession],
    source_ids: set[uuid.UUID],
) -> list[FeedSyncRun]:
    async with session_factory() as db:
        return list(
            (
                await db.scalars(
                    select(FeedSyncRun)
                    .where(FeedSyncRun.feed_source_id.in_(source_ids))
                    .order_by(FeedSyncRun.id)
                )
            ).all()
        )


async def _noop() -> None:
    return None
