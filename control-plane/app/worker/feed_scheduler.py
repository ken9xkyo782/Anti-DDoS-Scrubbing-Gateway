import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AgentJob, ChangeTrigger, GlobalDenyState, JobStatus, JobType
from app.db.session import (
    add_post_commit_callback,
    discard_post_commit_callbacks,
    run_post_commit_callbacks,
)
from app.services.apply import ApplyDispatcher
from app.services.feeds import enqueue_sync, list_due_sources

GLOBAL_DENY_TARGET_TYPE = "global_deny"
DEFAULT_DUE_SOURCE_LIMIT = 100


async def enqueue_due_feed_syncs(
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime,
    *,
    limit: int = DEFAULT_DUE_SOURCE_LIMIT,
) -> int:
    """Persist due feed runs and a pending global-deny convergence retry."""
    async with session_factory() as db:
        try:
            sources = await list_due_sources(db, now, limit)
            for source in sources:
                await enqueue_sync(
                    db,
                    source,
                    trigger=ChangeTrigger.feed_schedule,
                    dry_run=False,
                    actor=None,
                )
            global_enqueued = await _enqueue_global_convergence(db)
            await db.commit()
        except Exception:
            discard_post_commit_callbacks(db)
            await db.rollback()
            raise
        await run_post_commit_callbacks(db)
    return len(sources) + int(global_enqueued)


async def _enqueue_global_convergence(db: AsyncSession) -> bool:
    state = (
        (
            await db.execute(
                select(GlobalDenyState)
                .where(GlobalDenyState.id == 1)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .one_or_none()
    )
    if state is None or not _state_needs_apply(state):
        return False

    active_job = (
        (
            await db.execute(
                select(AgentJob.id).where(
                    AgentJob.job_type == JobType.global_deny_apply,
                    AgentJob.status.in_((JobStatus.queued, JobStatus.applying)),
                )
            )
        )
        .scalars()
        .first()
    )
    if active_job is not None:
        return False

    job = (
        (
            await db.execute(
                select(AgentJob)
                .where(
                    AgentJob.job_type == JobType.global_deny_apply,
                    AgentJob.version == state.desired_revision,
                )
                .with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )
    if job is None:
        job = AgentJob(
            target_type=GLOBAL_DENY_TARGET_TYPE,
            version=state.desired_revision,
            job_type=JobType.global_deny_apply,
            trigger=ChangeTrigger.global_deny_retry,
            status=JobStatus.queued,
        )
        db.add(job)
    else:
        job.status = JobStatus.queued
        job.error = None
        job.started_at = None
        job.finished_at = None
        job.dispatched_at = None
    await db.flush()
    _register_dispatch(db, job.id)
    return True


def _register_dispatch(db: AsyncSession, job_id: uuid.UUID) -> None:
    async def dispatch() -> None:
        await ApplyDispatcher().dispatch(job_id)

    add_post_commit_callback(db, dispatch)


def _state_needs_apply(state: GlobalDenyState) -> bool:
    return (
        state.desired_revision != state.active_revision
        or state.desired_digest != state.active_digest
    )
