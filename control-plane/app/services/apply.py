import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import desc, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.applystate import assert_active_version_advances, assert_transition, is_terminal
from app.core.redis import get_redis_client
from app.db.models import (
    AgentJob,
    ApplyStatus,
    ChangeTrigger,
    JobStatus,
    JobType,
    ProtectedService,
    Tenant,
    User,
    utc_now,
)
from app.db.session import add_post_commit_callback, get_session_factory
from app.services.audit import record_event

APPLY_QUEUE_KEY: Final = "apply:jobs"
APPLY_ERROR_LIMIT: Final = 2000
SERVICE_TARGET_TYPE: Final = "service"
_DISPATCH_JOB_IDS_KEY: Final = "apply_dispatch_job_ids"

logger = logging.getLogger(__name__)


class NotFailedError(ValueError):
    pass


@dataclass(frozen=True)
class ApplyStatusRecord:
    service_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None
    apply_status: ApplyStatus
    version: int
    active_version: int | None
    last_error: str | None
    last_applied_at: datetime | None
    latest_job: AgentJob | None


async def enqueue_service_update(
    db: AsyncSession,
    service: ProtectedService,
    actor: User | None,
    trigger: ChangeTrigger,
) -> AgentJob:
    del actor

    if service.apply_status != ApplyStatus.queued:
        assert_transition(service.apply_status, ApplyStatus.queued)

    job = await _insert_or_get_job(db, service=service, trigger=trigger)
    service.apply_status = ApplyStatus.queued
    service.updated_at = utc_now()
    await db.flush()
    _register_dispatch(db, job.id)
    return job


class ApplyDispatcher:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    async def dispatch(self, job_id: uuid.UUID, *, db: AsyncSession | None = None) -> None:
        redis = self._redis or get_redis_client()
        try:
            await redis.lpush(APPLY_QUEUE_KEY, str(job_id))
        except RedisError:
            logger.exception("Failed to dispatch apply job %s to Redis", job_id)
            return

        if db is not None:
            await self._mark_dispatched(db, job_id)
            return

        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                await self._mark_dispatched(session, job_id)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Failed to mark apply job %s dispatched", job_id)

    @staticmethod
    async def _mark_dispatched(db: AsyncSession, job_id: uuid.UUID) -> None:
        await db.execute(
            update(AgentJob).where(AgentJob.id == job_id).values(dispatched_at=utc_now())
        )
        await db.flush()


async def get_apply_status(
    db: AsyncSession,
    service: ProtectedService,
) -> ApplyStatusRecord:
    latest_job = await _latest_job(db, service.id)
    last_success = await _latest_success(db, service.id)
    tenant_name = (
        await db.execute(select(Tenant.name).where(Tenant.id == service.tenant_id))
    ).scalar_one_or_none()
    last_error = (
        latest_job.error
        if latest_job is not None and latest_job.status == JobStatus.failed
        else None
    )
    return ApplyStatusRecord(
        service_id=service.id,
        tenant_id=service.tenant_id,
        tenant_name=tenant_name,
        apply_status=service.apply_status,
        version=service.version,
        active_version=service.active_version,
        last_error=last_error,
        last_applied_at=last_success.finished_at if last_success is not None else None,
        latest_job=latest_job,
    )


async def list_jobs(
    db: AsyncSession,
    *,
    status: JobStatus | None = None,
) -> list[AgentJob]:
    statement = select(AgentJob)
    if status is not None:
        statement = statement.where(AgentJob.status == status)
    return list(
        (await db.execute(statement.order_by(desc(AgentJob.created_at), desc(AgentJob.version))))
        .scalars()
        .all()
    )


async def mark_applying(db: AsyncSession, job_id: uuid.UUID) -> None:
    job = await _load_job_for_update(db, job_id)
    if is_terminal(job.status):
        return
    service = await _load_service_for_update(db, job.target_id)
    if _superseded(service, job):
        _mark_superseded(job)
        await db.flush()
        return

    assert_transition(service.apply_status, ApplyStatus.applying)
    service.apply_status = ApplyStatus.applying
    service.updated_at = utc_now()
    job.status = JobStatus.applying
    job.started_at = utc_now()
    job.attempts += 1
    await db.flush()


async def mark_active(db: AsyncSession, job_id: uuid.UUID) -> None:
    job = await _load_job_for_update(db, job_id)
    if is_terminal(job.status):
        return
    service = await _load_service_for_update(db, job.target_id)
    if _superseded(service, job):
        _mark_superseded(job)
        await db.flush()
        return

    assert_transition(service.apply_status, ApplyStatus.active)
    assert_active_version_advances(service.active_version, job.version)
    service.apply_status = ApplyStatus.active
    service.active_version = job.version
    service.updated_at = utc_now()
    job.status = JobStatus.succeeded
    job.finished_at = utc_now()
    await db.flush()


async def mark_failed(db: AsyncSession, job_id: uuid.UUID, error: str) -> None:
    job = await _load_job_for_update(db, job_id)
    if is_terminal(job.status):
        return
    service = await _load_service_for_update(db, job.target_id)
    if _superseded(service, job):
        _mark_superseded(job)
        await db.flush()
        return

    assert_transition(service.apply_status, ApplyStatus.failed)
    service.apply_status = ApplyStatus.failed
    service.updated_at = utc_now()
    job.status = JobStatus.failed
    job.error = error[:APPLY_ERROR_LIMIT]
    job.finished_at = utc_now()
    await db.flush()


async def retry(db: AsyncSession, service: ProtectedService, actor: User | None) -> AgentJob:
    locked = await _load_service_for_update(db, service.id)
    if locked.apply_status != ApplyStatus.failed:
        raise NotFailedError("Apply status must be failed to retry")

    assert_transition(locked.apply_status, ApplyStatus.queued)
    job = await _current_version_job_for_update(db, locked)
    if job is None:
        job = AgentJob(
            target_type=SERVICE_TARGET_TYPE,
            target_id=locked.id,
            version=locked.version,
            job_type=JobType.service_update,
            trigger=ChangeTrigger.service,
            status=JobStatus.queued,
        )
        db.add(job)
        await db.flush()
    else:
        job.status = JobStatus.queued
        job.error = None
        job.started_at = None
        job.finished_at = None
        job.dispatched_at = None

    locked.apply_status = ApplyStatus.queued
    locked.updated_at = utc_now()
    await record_event(
        db,
        actor=actor,
        action="apply.retry",
        target_type="protected_service",
        target_id=str(locked.id),
        outcome="success",
        metadata={"version": locked.version},
    )
    await db.flush()
    _register_dispatch(db, job.id)
    return job


async def _insert_or_get_job(
    db: AsyncSession,
    *,
    service: ProtectedService,
    trigger: ChangeTrigger,
) -> AgentJob:
    job_id = uuid.uuid4()
    statement = (
        pg_insert(AgentJob)
        .values(
            id=job_id,
            target_type=SERVICE_TARGET_TYPE,
            target_id=service.id,
            version=service.version,
            job_type=JobType.service_update,
            trigger=trigger,
            status=JobStatus.queued,
            attempts=0,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(
            constraint="agent_job_target_version_unique",
        )
        .returning(AgentJob.id)
    )
    inserted_id = (await db.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        job = await db.get(AgentJob, inserted_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Apply job not found after insert",
            )
        return job

    existing = await _job_for_target_version(db, service.id, service.version)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Apply job not found after conflict",
        )
    return existing


async def _job_for_target_version(
    db: AsyncSession,
    service_id: uuid.UUID,
    version: int,
) -> AgentJob | None:
    return (
        (
            await db.execute(
                select(AgentJob).where(
                    AgentJob.target_type == SERVICE_TARGET_TYPE,
                    AgentJob.target_id == service_id,
                    AgentJob.version == version,
                )
            )
        )
        .scalars()
        .one_or_none()
    )


async def _current_version_job_for_update(
    db: AsyncSession,
    service: ProtectedService,
) -> AgentJob | None:
    return (
        (
            await db.execute(
                select(AgentJob)
                .where(
                    AgentJob.target_type == SERVICE_TARGET_TYPE,
                    AgentJob.target_id == service.id,
                    AgentJob.version == service.version,
                )
                .with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _latest_job(db: AsyncSession, service_id: uuid.UUID) -> AgentJob | None:
    return (
        (
            await db.execute(
                select(AgentJob)
                .where(
                    AgentJob.target_type == SERVICE_TARGET_TYPE,
                    AgentJob.target_id == service_id,
                )
                .order_by(desc(AgentJob.version), desc(AgentJob.created_at))
                .limit(1)
            )
        )
        .scalars()
        .one_or_none()
    )


async def _latest_success(db: AsyncSession, service_id: uuid.UUID) -> AgentJob | None:
    return (
        (
            await db.execute(
                select(AgentJob)
                .where(
                    AgentJob.target_type == SERVICE_TARGET_TYPE,
                    AgentJob.target_id == service_id,
                    AgentJob.status == JobStatus.succeeded,
                )
                .order_by(desc(AgentJob.version), desc(AgentJob.finished_at))
                .limit(1)
            )
        )
        .scalars()
        .one_or_none()
    )


async def _load_job_for_update(db: AsyncSession, job_id: uuid.UUID) -> AgentJob:
    job = (
        (await db.execute(select(AgentJob).where(AgentJob.id == job_id).with_for_update()))
        .scalars()
        .one_or_none()
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Apply job not found")
    return job


async def _load_service_for_update(
    db: AsyncSession,
    service_id: uuid.UUID,
) -> ProtectedService:
    service = (
        (
            await db.execute(
                select(ProtectedService).where(ProtectedService.id == service_id).with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


def _register_dispatch(db: AsyncSession, job_id: uuid.UUID) -> None:
    job_ids = db.info.setdefault(_DISPATCH_JOB_IDS_KEY, set())
    if job_id in job_ids:
        return
    job_ids.add(job_id)

    async def dispatch() -> None:
        job_ids.discard(job_id)
        await ApplyDispatcher().dispatch(job_id)

    add_post_commit_callback(db, dispatch)


def _superseded(service: ProtectedService, job: AgentJob) -> bool:
    return service.version != job.version


def _mark_superseded(job: AgentJob) -> None:
    job.status = JobStatus.superseded
    job.finished_at = utc_now()
