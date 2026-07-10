import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentJob, ChangeTrigger, JobStatus, JobType, ProtectedService, Tenant

pytestmark = pytest.mark.integration


async def create_service(db_session: AsyncSession) -> ProtectedService:
    tenant = Tenant(name="Agent Job Tenant")
    service = ProtectedService(
        tenant=tenant,
        name="edge",
        cidr_or_ip="203.0.113.10/32",
    )
    db_session.add_all([tenant, service])
    await db_session.flush()
    return service


async def test_agent_job_indexes_exist(db_session: AsyncSession) -> None:
    indexes = (
        await db_session.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'agent_job'
                  AND indexname IN (
                      'ix_agent_job_status',
                      'ix_agent_job_target',
                      'uq_agent_job_service_target_version'
                  )
                ORDER BY indexname
                """
            )
        )
    ).scalars()

    assert list(indexes) == [
        "ix_agent_job_status",
        "ix_agent_job_target",
        "uq_agent_job_service_target_version",
    ]


async def test_agent_job_target_version_is_unique(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    db_session.add_all(
        [
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=1,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.queued,
            ),
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=1,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
                status=JobStatus.queued,
            ),
        ]
    )

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "uq_agent_job_service_target_version" in str(exc_info.value)


async def test_delete_service_cascades_agent_jobs(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    db_session.add(
        AgentJob(
            target_type="service",
            target_id=service.id,
            version=1,
            job_type=JobType.service_update,
            trigger=ChangeTrigger.service,
            status=JobStatus.queued,
        )
    )
    await db_session.flush()

    await db_session.delete(service)
    await db_session.flush()

    assert (await db_session.execute(select(func.count(AgentJob.id)))).scalar_one() == 0
