import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.applystate import is_terminal
from app.db.models import (
    AgentJob,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    JobType,
    ProtectedService,
    ThreatFeedSource,
    utc_now,
)
from app.services.apply import APPLY_ERROR_LIMIT, mark_active, mark_applying, mark_failed, retry

logger = logging.getLogger(__name__)

ORPHAN_ERROR: Final = "worker restarted mid-apply"
MAX_RECOVERY_ATTEMPTS: Final = 3
_SECRET_VALUE = re.compile(
    r"(?i)(?P<key>\b[\w-]*(?:password|token|secret|credential|authorization)[\w-]*\b"
    r"\s*(?:=|:)\s*)(?P<value>[^\s,;]+)"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@")


class JobLifecycle(Protocol):
    async def claim(self, db: AsyncSession, job: AgentJob) -> bool: ...

    async def succeed(
        self,
        db: AsyncSession,
        job: AgentJob,
        outcome: FeedSyncStatus | None,
    ) -> None: ...

    async def fail(self, db: AsyncSession, job: AgentJob, error: str) -> None: ...

    async def recover(self, db: AsyncSession, job: AgentJob) -> None: ...


class ServiceJobLifecycle:
    async def claim(self, db: AsyncSession, job: AgentJob) -> bool:
        await mark_applying(db, job.id)
        claimed = await db.get(AgentJob, job.id, populate_existing=True)
        return claimed is not None and claimed.status == JobStatus.applying

    async def succeed(
        self,
        db: AsyncSession,
        job: AgentJob,
        outcome: FeedSyncStatus | None,
    ) -> None:
        del outcome
        await mark_active(db, job.id)

    async def fail(self, db: AsyncSession, job: AgentJob, error: str) -> None:
        await mark_failed(db, job.id, error)

    async def recover(self, db: AsyncSession, job: AgentJob) -> None:
        await mark_failed(db, job.id, ORPHAN_ERROR)
        service = await db.get(ProtectedService, job.target_id)
        if service is None:
            logger.warning("Orphaned job service missing", extra={"job_id": str(job.id)})
            return
        await retry(db, service, actor=None)


class FeedSyncJobLifecycle:
    async def claim(self, db: AsyncSession, job: AgentJob) -> bool:
        locked_job = await _locked_job(db, job.id)
        if locked_job is None or is_terminal(locked_job.status):
            return False
        if locked_job.status != JobStatus.queued:
            return False

        run = await _locked_run(db, locked_job.feed_sync_run_id)
        if run is None:
            await _finish_noop(db, locked_job, None)
            return False
        source = await _locked_source(db, run.feed_source_id)
        if source is None or source.deleted_at is not None:
            await _finish_noop(db, locked_job, run)
            return False
        if run.status != FeedSyncStatus.queued:
            return False

        now = utc_now()
        locked_job.status = JobStatus.applying
        locked_job.error = None
        locked_job.started_at = now
        locked_job.attempts += 1
        run.status = FeedSyncStatus.running
        run.error = None
        run.started_at = now
        run.finished_at = None
        run.duration_ms = None
        await db.flush()
        return True

    async def succeed(
        self,
        db: AsyncSession,
        job: AgentJob,
        outcome: FeedSyncStatus | None,
    ) -> None:
        locked_job = await _locked_job(db, job.id)
        if locked_job is None or is_terminal(locked_job.status):
            return
        if locked_job.status != JobStatus.applying:
            return

        run = await _locked_run(db, locked_job.feed_sync_run_id)
        if run is None:
            await _finish_noop(db, locked_job, None)
            return
        source = await _locked_source(db, run.feed_source_id)
        if source is None or source.deleted_at is not None:
            await _finish_noop(db, locked_job, run)
            return
        if run.status != FeedSyncStatus.running:
            return

        final_status = outcome or FeedSyncStatus.success
        if final_status not in {FeedSyncStatus.success, FeedSyncStatus.partial}:
            raise ValueError("Feed success outcome must be success or partial")

        finished_at = utc_now()
        locked_job.status = JobStatus.succeeded
        locked_job.error = None
        locked_job.finished_at = finished_at
        run.status = final_status
        run.error = None
        run.finished_at = finished_at
        run.duration_ms = _duration_ms(run.started_at, finished_at)
        source.last_status = final_status
        source.last_error = None
        source.last_sync_at = finished_at
        source.next_sync_at = finished_at + timedelta(seconds=source.sync_interval_seconds)
        await db.flush()

    async def fail(self, db: AsyncSession, job: AgentJob, error: str) -> None:
        locked_job = await _locked_job(db, job.id)
        if locked_job is None or is_terminal(locked_job.status):
            return
        if locked_job.status != JobStatus.applying:
            return

        run = await _locked_run(db, locked_job.feed_sync_run_id)
        if run is None:
            await _finish_noop(db, locked_job, None)
            return
        source = await _locked_source(db, run.feed_source_id)
        if source is None or source.deleted_at is not None:
            await _finish_noop(db, locked_job, run)
            return
        if run.status != FeedSyncStatus.running:
            return

        finished_at = utc_now()
        safe_error = _scrub_error(error)
        locked_job.status = JobStatus.failed
        locked_job.error = safe_error
        locked_job.finished_at = finished_at
        run.status = FeedSyncStatus.failed
        run.error = safe_error
        run.finished_at = finished_at
        run.duration_ms = _duration_ms(run.started_at, finished_at)
        source.last_status = FeedSyncStatus.failed
        source.last_error = safe_error
        source.last_sync_at = finished_at
        source.next_sync_at = finished_at + timedelta(seconds=source.sync_interval_seconds)
        await db.flush()

    async def recover(self, db: AsyncSession, job: AgentJob) -> None:
        await self.fail(db, job, ORPHAN_ERROR)
        locked_job = await _locked_job(db, job.id)
        if (
            locked_job is None
            or locked_job.status != JobStatus.failed
            or locked_job.attempts >= MAX_RECOVERY_ATTEMPTS
        ):
            return

        run = await _locked_run(db, locked_job.feed_sync_run_id)
        if run is None:
            return
        source = await _locked_source(db, run.feed_source_id)
        if source is None or source.deleted_at is not None:
            return

        locked_job.status = JobStatus.queued
        locked_job.error = None
        locked_job.started_at = None
        locked_job.finished_at = None
        locked_job.dispatched_at = None
        run.status = FeedSyncStatus.queued
        run.error = None
        run.started_at = None
        run.finished_at = None
        run.duration_ms = None
        await db.flush()


class GlobalDenyJobLifecycle:
    async def claim(self, db: AsyncSession, job: AgentJob) -> bool:
        locked_job = await _locked_job(db, job.id)
        if (
            locked_job is None
            or is_terminal(locked_job.status)
            or locked_job.status != JobStatus.queued
        ):
            return False

        locked_job.status = JobStatus.applying
        locked_job.error = None
        locked_job.started_at = utc_now()
        locked_job.attempts += 1
        await db.flush()
        return True

    async def succeed(
        self,
        db: AsyncSession,
        job: AgentJob,
        outcome: FeedSyncStatus | None,
    ) -> None:
        del outcome
        locked_job = await _locked_job(db, job.id)
        if locked_job is None or is_terminal(locked_job.status):
            return
        if locked_job.status != JobStatus.applying:
            return

        locked_job.status = JobStatus.succeeded
        locked_job.error = None
        locked_job.finished_at = utc_now()
        await db.flush()

    async def fail(self, db: AsyncSession, job: AgentJob, error: str) -> None:
        locked_job = await _locked_job(db, job.id)
        if locked_job is None or is_terminal(locked_job.status):
            return
        if locked_job.status != JobStatus.applying:
            return

        locked_job.status = JobStatus.failed
        locked_job.error = _scrub_error(error)
        locked_job.finished_at = utc_now()
        await db.flush()

    async def recover(self, db: AsyncSession, job: AgentJob) -> None:
        await self.fail(db, job, ORPHAN_ERROR)
        locked_job = await _locked_job(db, job.id)
        if (
            locked_job is None
            or locked_job.status != JobStatus.failed
            or locked_job.attempts >= MAX_RECOVERY_ATTEMPTS
        ):
            return

        locked_job.status = JobStatus.queued
        locked_job.error = None
        locked_job.started_at = None
        locked_job.finished_at = None
        locked_job.dispatched_at = None
        await db.flush()


JOB_LIFECYCLES: dict[JobType, JobLifecycle] = {
    JobType.service_update: ServiceJobLifecycle(),
    JobType.feed_sync: FeedSyncJobLifecycle(),
    JobType.global_deny_apply: GlobalDenyJobLifecycle(),
}


async def _locked_job(db: AsyncSession, job_id: uuid.UUID) -> AgentJob | None:
    return (
        (await db.execute(select(AgentJob).where(AgentJob.id == job_id).with_for_update()))
        .scalars()
        .one_or_none()
    )


async def _locked_run(db: AsyncSession, run_id: uuid.UUID | None) -> FeedSyncRun | None:
    if run_id is None:
        return None
    return (
        (await db.execute(select(FeedSyncRun).where(FeedSyncRun.id == run_id).with_for_update()))
        .scalars()
        .one_or_none()
    )


async def _locked_source(db: AsyncSession, source_id: uuid.UUID) -> ThreatFeedSource | None:
    return (
        (
            await db.execute(
                select(ThreatFeedSource).where(ThreatFeedSource.id == source_id).with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _finish_noop(
    db: AsyncSession,
    job: AgentJob,
    run: FeedSyncRun | None,
) -> None:
    finished_at = utc_now()
    job.status = JobStatus.succeeded
    job.error = None
    job.finished_at = finished_at
    if run is not None:
        run.status = FeedSyncStatus.success
        run.error = None
        run.finished_at = finished_at
        run.duration_ms = _duration_ms(run.started_at, finished_at)
    await db.flush()


def _duration_ms(started_at: datetime | None, finished_at: datetime) -> int | None:
    if started_at is None:
        return None
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _scrub_error(error: str) -> str:
    scrubbed = _URL_USERINFO.sub(r"\1[redacted]@", error)
    scrubbed = _BEARER_VALUE.sub("Bearer [redacted]", scrubbed)
    scrubbed = _SECRET_VALUE.sub(r"\g<key>[redacted]", scrubbed)
    return scrubbed[:APPLY_ERROR_LIMIT]
