import asyncio
import logging
import time
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import AgentJob, ApplyStatus, ChangeTrigger, JobStatus, ProtectedService, Tenant
from app.db.session import session_scope
from app.services.apply import enqueue_service_update
from app.worker.applier import ServiceConfig
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


def runtime_settings(**values: float) -> Settings:
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


async def get_service(
    session_factory: async_sessionmaker[AsyncSession],
    service_id: uuid.UUID,
) -> ProtectedService:
    async with session_factory() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        return service


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


async def _noop() -> None:
    return None
