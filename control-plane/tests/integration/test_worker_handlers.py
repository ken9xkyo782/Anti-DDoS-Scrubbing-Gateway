import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentJob, ChangeTrigger, JobType, ProtectedService, Tenant
from app.worker.applier import ServiceConfig
from app.worker.handlers import HANDLERS, handle_service_update

pytestmark = pytest.mark.integration


class RecordingApplier:
    def __init__(self) -> None:
        self.config: ServiceConfig | None = None

    async def apply(self, config: ServiceConfig) -> None:
        self.config = config


async def create_service_and_job(db_session: AsyncSession) -> tuple[ProtectedService, AgentJob]:
    tenant = Tenant(name="Worker Handler Tenant")
    service = ProtectedService(
        tenant=tenant,
        name="handler-service",
        cidr_or_ip="203.0.113.20/32",
        version=4,
    )
    db_session.add_all([tenant, service])
    await db_session.flush()

    job = AgentJob(
        target_type="service",
        target_id=service.id,
        version=service.version,
        job_type=JobType.service_update,
        trigger=ChangeTrigger.service,
    )
    db_session.add(job)
    await db_session.flush()
    return service, job


async def test_handle_service_update_applies_target_config(db_session: AsyncSession) -> None:
    service, job = await create_service_and_job(db_session)
    applier = RecordingApplier()

    await handle_service_update(db_session, job, applier)

    assert applier.config is not None
    assert applier.config.service_id == service.id
    assert applier.config.version == service.version


async def test_handle_service_update_raises_when_service_is_missing(
    db_session: AsyncSession,
) -> None:
    job = AgentJob(
        target_type="service",
        target_id=uuid.uuid4(),
        version=1,
        job_type=JobType.service_update,
        trigger=ChangeTrigger.service,
    )

    with pytest.raises(RuntimeError, match="service missing"):
        await handle_service_update(db_session, job, RecordingApplier())


def test_service_update_job_type_resolves_handler() -> None:
    assert HANDLERS[JobType.service_update] is handle_service_update
