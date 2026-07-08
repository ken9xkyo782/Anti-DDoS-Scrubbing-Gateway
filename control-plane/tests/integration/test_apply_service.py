import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentJob,
    ApplyStatus,
    ChangeTrigger,
    JobStatus,
    JobType,
    ProtectedService,
    Role,
    Tenant,
    User,
)
from app.services import apply as apply_service

pytestmark = pytest.mark.integration


async def create_actor_and_service(db_session: AsyncSession) -> tuple[User, ProtectedService]:
    tenant = Tenant(name="Apply Service Tenant")
    actor = User(username="apply-service-admin", role=Role.admin, password_hash="$argon2id$hash")
    service = ProtectedService(
        tenant=tenant,
        name="edge",
        cidr_or_ip="203.0.113.10/32",
        apply_status=ApplyStatus.pending,
        version=1,
    )
    db_session.add_all([tenant, actor, service])
    await db_session.flush()
    return actor, service


async def latest_job(db_session: AsyncSession, service: ProtectedService) -> AgentJob:
    return (
        await db_session.execute(
            select(AgentJob)
            .where(AgentJob.target_id == service.id)
            .order_by(AgentJob.version.desc())
        )
    ).scalar_one()


async def test_enqueue_service_update_is_idempotent_and_queues_service(
    db_session: AsyncSession,
) -> None:
    actor, service = await create_actor_and_service(db_session)

    first = await apply_service.enqueue_service_update(
        db_session,
        service,
        actor,
        ChangeTrigger.service,
    )
    second = await apply_service.enqueue_service_update(
        db_session,
        service,
        actor,
        ChangeTrigger.service,
    )

    assert first.id == second.id
    assert service.apply_status == ApplyStatus.queued
    assert (await db_session.execute(select(func.count(AgentJob.id)))).scalar_one() == 1


async def test_dispatch_pushes_redis_and_marks_dispatched(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    actor, service = await create_actor_and_service(db_session)
    job = await apply_service.enqueue_service_update(
        db_session,
        service,
        actor,
        ChangeTrigger.service,
    )

    await apply_service.ApplyDispatcher(redis_client).dispatch(job.id, db=db_session)

    assert await redis_client.lrange(apply_service.APPLY_QUEUE_KEY, 0, -1) == [str(job.id)]
    assert job.dispatched_at is not None


async def test_mark_applying_active_and_failed_paths(db_session: AsyncSession) -> None:
    actor, service = await create_actor_and_service(db_session)
    job = await apply_service.enqueue_service_update(
        db_session,
        service,
        actor,
        ChangeTrigger.service,
    )

    await apply_service.mark_applying(db_session, job.id)
    assert service.apply_status == ApplyStatus.applying
    assert job.status == JobStatus.applying
    assert job.attempts == 1

    await apply_service.mark_active(db_session, job.id)
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded

    await apply_service.mark_active(db_session, job.id)
    assert service.active_version == 1


async def test_mark_failed_keeps_active_version_and_retry_requeues(
    db_session: AsyncSession,
) -> None:
    actor, service = await create_actor_and_service(db_session)
    service.active_version = 0
    job = await apply_service.enqueue_service_update(
        db_session,
        service,
        actor,
        ChangeTrigger.service,
    )
    await apply_service.mark_applying(db_session, job.id)

    await apply_service.mark_failed(db_session, job.id, "x" * 3000)
    await apply_service.retry(db_session, service, actor)

    assert service.active_version == 0
    assert service.apply_status == ApplyStatus.queued
    assert job.status == JobStatus.queued
    assert job.error is None


async def test_stale_job_is_superseded_without_advancing_active_version(
    db_session: AsyncSession,
) -> None:
    actor, service = await create_actor_and_service(db_session)
    stale = await apply_service.enqueue_service_update(
        db_session,
        service,
        actor,
        ChangeTrigger.service,
    )
    await apply_service.mark_applying(db_session, stale.id)
    service.version = 2
    service.apply_status = ApplyStatus.queued
    current = AgentJob(
        target_type="service",
        target_id=service.id,
        version=2,
        job_type=JobType.service_update,
        trigger=ChangeTrigger.rule,
        status=JobStatus.queued,
    )
    db_session.add(current)
    await db_session.flush()

    await apply_service.mark_active(db_session, stale.id)
    await apply_service.mark_applying(db_session, current.id)
    await apply_service.mark_active(db_session, current.id)

    assert stale.status == JobStatus.superseded
    assert service.active_version == 2
