import logging
import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AgentJob, JobStatus
from app.db.session import session_scope
from app.worker.applier import Applier
from app.worker.feed_jobs import JOB_LIFECYCLES
from app.worker.handlers import HANDLERS, NoHandlerError

logger = logging.getLogger(__name__)

TERMINAL_MARK_ATTEMPTS: Final = 3


async def process_job(
    job_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    applier: Applier,
) -> None:
    """Process one durable job without holding a transaction across its handler."""
    async with session_scope() as db:
        job = await db.get(AgentJob, job_id)
        if job is None:
            logger.warning("Apply job missing from ledger", extra={"job_id": str(job_id)})
            return

        lifecycle = JOB_LIFECYCLES[job.job_type]
        proceed = await lifecycle.claim(db, job)

    if not proceed:
        return

    error: str | None = None
    try:
        async with session_scope() as db:
            job = await db.get(AgentJob, job_id)
            if job is None:
                logger.warning("Apply job missing from ledger", extra={"job_id": str(job_id)})
                return
            handler = HANDLERS.get(job.job_type)
            if handler is None:
                raise NoHandlerError(f"No handler for job type {job.job_type}")
        await handler(job, applier)
    except SQLAlchemyError:
        raise
    except Exception as exc:
        logger.exception("Apply job handler failed", extra={"job_id": str(job_id)})
        error = f"{type(exc).__name__}: {exc}"

    await _mark_terminal(job_id, error)


async def reconcile_once(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    applier: Applier,
    include_orphans: bool,
) -> int:
    """Process queued work and optionally recover startup-time applying orphans."""
    async with session_factory() as db:
        queued_ids = list(
            (
                await db.scalars(
                    select(AgentJob.id)
                    .where(AgentJob.status == JobStatus.queued)
                    .order_by(AgentJob.created_at.asc(), AgentJob.id.asc())
                )
            ).all()
        )

    processed = 0
    for job_id in queued_ids:
        await process_job(job_id, session_factory=session_factory, applier=applier)
        processed += 1

    if not include_orphans:
        return processed

    async with session_factory() as db:
        applying_ids = list(
            (
                await db.scalars(
                    select(AgentJob.id)
                    .where(AgentJob.status == JobStatus.applying)
                    .order_by(AgentJob.created_at.asc(), AgentJob.id.asc())
                )
            ).all()
        )

    for job_id in applying_ids:
        async with session_scope() as db:
            job = await db.get(AgentJob, job_id)
            if job is None or job.status != JobStatus.applying:
                continue
            await recover_orphan(db, job)
        processed += 1

    return processed


async def recover_orphan(db: AsyncSession, job: AgentJob) -> None:
    """Dispatch startup recovery to the durable job's target-aware lifecycle."""
    await JOB_LIFECYCLES[job.job_type].recover(db, job)


async def _mark_terminal(job_id: uuid.UUID, error: str | None) -> None:
    for attempt in range(TERMINAL_MARK_ATTEMPTS):
        try:
            async with session_scope() as db:
                job = await db.get(AgentJob, job_id)
                if job is None:
                    logger.warning("Apply job missing from ledger", extra={"job_id": str(job_id)})
                    return
                lifecycle = JOB_LIFECYCLES[job.job_type]
                if error is None:
                    await lifecycle.succeed(db, job, None)
                else:
                    await lifecycle.fail(db, job, error)
            return
        except OperationalError:
            if attempt + 1 == TERMINAL_MARK_ATTEMPTS:
                raise
            logger.warning(
                "Retrying terminal apply mark after database error",
                extra={"job_id": str(job_id), "attempt": attempt + 1},
            )
