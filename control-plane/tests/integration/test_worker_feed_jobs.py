import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AgentJob,
    ApplyStatus,
    ChangeTrigger,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    JobType,
    ProtectedService,
    Tenant,
    ThreatFeedSource,
)
from app.db.session import session_scope
from app.services.apply import APPLY_ERROR_LIMIT
from app.worker.feed_jobs import JOB_LIFECYCLES, MAX_RECOVERY_ATTEMPTS

pytestmark = pytest.mark.integration


async def seed_feed_job(
    *,
    name: str,
    include_service: bool = False,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID | None]:
    async with session_scope() as db:
        source = ThreatFeedSource(
            name=name,
            url="https://feeds.example.test/deny.txt",
            sync_interval_seconds=300,
        )
        db.add(source)
        await db.flush()
        run = FeedSyncRun(
            feed_source_id=source.id,
            source_name=source.name,
            sequence=1,
            trigger=ChangeTrigger.feed_manual,
        )
        db.add(run)
        await db.flush()

        service_id: uuid.UUID | None = None
        if include_service:
            tenant = Tenant(name=f"{name}-tenant")
            service = ProtectedService(
                tenant=tenant,
                name=f"{name}-service",
                cidr_or_ip="203.0.113.210/32",
                apply_status=ApplyStatus.active,
                active_version=4,
                version=4,
            )
            db.add_all([tenant, service])
            await db.flush()
            service_id = service.id

        job = AgentJob(
            target_type="feed_sync_run",
            feed_sync_run_id=run.id,
            version=run.sequence,
            job_type=JobType.feed_sync,
            trigger=ChangeTrigger.feed_manual,
            status=JobStatus.queued,
        )
        db.add(job)
        await db.flush()
    return job.id, run.id, source.id, service_id


async def seed_global_job(*, version: int = 1) -> uuid.UUID:
    async with session_scope() as db:
        job = AgentJob(
            target_type="global_deny",
            version=version,
            job_type=JobType.global_deny_apply,
            trigger=ChangeTrigger.global_deny_retry,
            status=JobStatus.queued,
        )
        db.add(job)
        await db.flush()
    return job.id


async def get_feed_records(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    source_id: uuid.UUID,
) -> tuple[AgentJob, FeedSyncRun, ThreatFeedSource]:
    async with session_factory() as db:
        job = await db.get(AgentJob, job_id)
        run = await db.get(FeedSyncRun, run_id)
        source = await db.get(ThreatFeedSource, source_id)
        assert job is not None
        assert run is not None
        assert source is not None
        return job, run, source


async def claim_feed_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        assert await JOB_LIFECYCLES[JobType.feed_sync].claim(db, job)


async def test_job_lifecycle_registry_covers_every_job_type() -> None:
    assert set(JOB_LIFECYCLES) == {
        JobType.service_update,
        JobType.feed_sync,
        JobType.global_deny_apply,
    }


async def test_feed_claim_marks_only_the_job_and_run(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id, run_id, source_id, service_id = await seed_feed_job(
        name="feed-claim",
        include_service=True,
    )

    await claim_feed_job(job_id)

    job, run, _ = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    async with committed_db() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
    assert job.status == JobStatus.applying
    assert job.attempts == 1
    assert job.started_at is not None
    assert run.status == FeedSyncStatus.running
    assert run.started_at is not None
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 4


@pytest.mark.parametrize("outcome", [FeedSyncStatus.success, FeedSyncStatus.partial])
async def test_feed_success_records_terminal_outcome_and_next_due(
    committed_db: async_sessionmaker[AsyncSession],
    outcome: FeedSyncStatus,
) -> None:
    job_id, run_id, source_id, _ = await seed_feed_job(name=f"feed-{outcome.value}")
    await claim_feed_job(job_id)

    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        await JOB_LIFECYCLES[JobType.feed_sync].succeed(db, job, outcome)

    job, run, source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    assert job.status == JobStatus.succeeded
    assert job.finished_at is not None
    assert job.error is None
    assert run.status == outcome
    assert run.finished_at is not None
    assert run.error is None
    assert source.last_status == outcome
    assert source.last_error is None
    assert source.last_sync_at == run.finished_at
    assert source.next_sync_at == run.finished_at + timedelta(seconds=source.sync_interval_seconds)


async def test_feed_failure_scrubs_caps_and_schedules_the_source(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id, run_id, source_id, _ = await seed_feed_job(name="feed-failure")
    await claim_feed_job(job_id)
    error = f"token=not-for-logs {'x' * APPLY_ERROR_LIMIT}"

    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        await JOB_LIFECYCLES[JobType.feed_sync].fail(db, job, error)

    job, run, source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    assert job.status == JobStatus.failed
    assert run.status == FeedSyncStatus.failed
    assert source.last_status == FeedSyncStatus.failed
    assert job.error is not None
    assert run.error == job.error
    assert source.last_error == job.error
    assert "not-for-logs" not in job.error
    assert len(job.error) <= APPLY_ERROR_LIMIT
    assert run.finished_at is not None
    assert source.next_sync_at == run.finished_at + timedelta(seconds=source.sync_interval_seconds)


async def test_feed_duplicate_terminal_delivery_is_a_no_op(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id, run_id, source_id, _ = await seed_feed_job(name="feed-duplicate")
    await claim_feed_job(job_id)
    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        await JOB_LIFECYCLES[JobType.feed_sync].succeed(db, job, FeedSyncStatus.success)
    first_job, first_run, first_source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )

    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        await JOB_LIFECYCLES[JobType.feed_sync].fail(db, job, "second delivery")

    job, run, source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    assert job.status == JobStatus.succeeded
    assert job.finished_at == first_job.finished_at
    assert run.status == FeedSyncStatus.success
    assert run.finished_at == first_run.finished_at
    assert source.last_status == FeedSyncStatus.success
    assert source.last_sync_at == first_source.last_sync_at


async def test_deleted_feed_source_claims_a_terminal_no_op(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id, run_id, source_id, _ = await seed_feed_job(name="feed-tombstone")
    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        source.deleted_at = source.updated_at

        job = await db.get(AgentJob, job_id)
        assert job is not None
        assert not await JOB_LIFECYCLES[JobType.feed_sync].claim(db, job)

    job, run, source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    assert job.status == JobStatus.succeeded
    assert run.status == FeedSyncStatus.success
    assert source.last_status is None
    assert source.next_sync_at is None


async def test_feed_orphan_requeues_the_same_run_within_budget(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id, run_id, source_id, _ = await seed_feed_job(name="feed-orphan")
    await claim_feed_job(job_id)

    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        await JOB_LIFECYCLES[JobType.feed_sync].recover(db, job)

    job, run, source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    assert job.status == JobStatus.queued
    assert job.attempts == 1
    assert job.error is None
    assert run.status == FeedSyncStatus.queued
    assert run.error is None
    assert run.started_at is None
    assert run.finished_at is None
    assert source.last_status == FeedSyncStatus.failed


async def test_feed_orphan_stops_when_recovery_budget_is_exhausted(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id, run_id, source_id, _ = await seed_feed_job(name="feed-orphan-exhausted")
    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        job.status = JobStatus.applying
        job.attempts = MAX_RECOVERY_ATTEMPTS
        run = await db.get(FeedSyncRun, run_id)
        assert run is not None
        run.status = FeedSyncStatus.running

        await JOB_LIFECYCLES[JobType.feed_sync].recover(db, job)

    job, run, source = await get_feed_records(
        committed_db,
        job_id=job_id,
        run_id=run_id,
        source_id=source_id,
    )
    assert job.status == JobStatus.failed
    assert run.status == FeedSyncStatus.failed
    assert source.last_status == FeedSyncStatus.failed


async def test_global_lifecycle_is_source_free(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await seed_global_job()
    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        assert await JOB_LIFECYCLES[JobType.global_deny_apply].claim(db, job)
        await JOB_LIFECYCLES[JobType.global_deny_apply].fail(db, job, "global token=hidden")

    async with committed_db() as db:
        job = await db.get(AgentJob, job_id)
        source_count = len((await db.scalars(select(ThreatFeedSource))).all())
        run_count = len((await db.scalars(select(FeedSyncRun))).all())
    assert job is not None
    assert job.status == JobStatus.failed
    assert job.error is not None
    assert "hidden" not in job.error
    assert source_count == 0
    assert run_count == 0


async def test_global_duplicate_terminal_delivery_is_a_no_op(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await seed_global_job(version=2)
    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        assert await JOB_LIFECYCLES[JobType.global_deny_apply].claim(db, job)
        await JOB_LIFECYCLES[JobType.global_deny_apply].succeed(db, job, None)

    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        assert job is not None
        assert not await JOB_LIFECYCLES[JobType.global_deny_apply].claim(db, job)
        await JOB_LIFECYCLES[JobType.global_deny_apply].fail(db, job, "late failure")

    async with committed_db() as db:
        job = await db.get(AgentJob, job_id)
    assert job is not None
    assert job.status == JobStatus.succeeded
    assert job.error is None
