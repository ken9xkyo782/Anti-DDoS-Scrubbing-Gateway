import asyncio
import logging
import time
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AgentJob,
    ApplyStatus,
    AuditEvent,
    ChangeTrigger,
    JobStatus,
    JobType,
    ProtectedService,
    Tenant,
)
from app.db.session import session_scope
from app.services.apply import APPLY_QUEUE_KEY, enqueue_service_update, mark_applying, retry
from app.services.node_control import set_maintenance
from app.services.services import bump_version
from app.worker.applier import ServiceConfig
from app.worker.handlers import configure_nexthop_resolver
from app.worker.processor import process_job, reconcile_once

pytestmark = pytest.mark.integration


class RecordingApplier:
    def __init__(self) -> None:
        self.versions: list[int] = []

    async def apply(self, config: ServiceConfig) -> None:
        self.versions.append(config.version)


class FailingApplier:
    async def apply(self, config: ServiceConfig) -> None:
        raise RuntimeError(f"apply failed for version {config.version}")


class BarrierApplier:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.versions: list[int] = []

    async def apply(self, config: ServiceConfig) -> None:
        self.versions.append(config.version)
        self.entered.set()
        await self.release.wait()


async def enqueue_job(
    *,
    name: str,
    cidr_or_ip: str,
    version: int = 1,
    active_version: int | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_scope() as db:
        tenant = Tenant(name=f"{name}-tenant")
        service = ProtectedService(
            tenant=tenant,
            name=name,
            cidr_or_ip=cidr_or_ip,
            apply_status=ApplyStatus.pending,
            version=version,
            active_version=active_version,
        )
        db.add_all([tenant, service])
        await db.flush()
        job = await enqueue_service_update(db, service, actor=None, trigger=ChangeTrigger.service)
    return service.id, job.id


async def seed_queued_job_without_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    cidr_or_ip: str,
    version: int,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_factory() as db:
        tenant = Tenant(name=f"{name}-tenant")
        service = ProtectedService(
            tenant=tenant,
            name=name,
            cidr_or_ip=cidr_or_ip,
            apply_status=ApplyStatus.queued,
            version=version,
        )
        db.add_all([tenant, service])
        await db.flush()
        job = AgentJob(
            target_type="service",
            target_id=service.id,
            version=version,
            job_type=JobType.service_update,
            trigger=ChangeTrigger.service,
            status=JobStatus.queued,
        )
        db.add(job)
        await db.commit()
    return service.id, job.id


async def get_service_and_job(
    session_factory: async_sessionmaker[AsyncSession],
    service_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[ProtectedService, AgentJob]:
    async with session_factory() as db:
        service = await db.get(ProtectedService, service_id)
        job = await db.get(AgentJob, job_id)
        assert service is not None
        assert job is not None
        return service, job


async def test_process_job_activates_enqueued_service_within_five_seconds(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    started = time.monotonic()
    service_id, job_id = await enqueue_job(
        name="processor-happy",
        cidr_or_ip="203.0.113.40/32",
    )
    applier = RecordingApplier()

    await process_job(job_id, session_factory=committed_db, applier=applier)

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert time.monotonic() - started <= 5
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded
    assert applier.versions == [1]


async def test_process_job_missing_ledger_id_logs_and_skips(
    committed_db: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.getLogger("app.worker.processor"), "disabled", False)
    caplog.set_level(logging.WARNING, logger="app.worker.processor")

    await process_job(uuid.uuid4(), session_factory=committed_db, applier=RecordingApplier())

    assert "Apply job missing from ledger" in caplog.text


async def test_process_job_superseded_before_claim_skips_handler(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await seed_queued_job_without_dispatch(
        committed_db,
        name="processor-stale",
        cidr_or_ip="203.0.113.41/32",
        version=1,
    )
    async with session_scope() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        await bump_version(db, service_id)
        await enqueue_service_update(db, service, actor=None, trigger=ChangeTrigger.rule)
    applier = RecordingApplier()

    await process_job(job_id, session_factory=committed_db, applier=applier)

    _, job = await get_service_and_job(committed_db, service_id, job_id)
    assert job.status == JobStatus.superseded
    assert applier.versions == []


async def test_process_job_no_handler_marks_job_failed(
    committed_db: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_id, job_id = await enqueue_job(
        name="processor-no-handler",
        cidr_or_ip="203.0.113.42/32",
    )
    monkeypatch.setattr("app.worker.processor.HANDLERS", {})

    await process_job(job_id, session_factory=committed_db, applier=RecordingApplier())

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert service.apply_status == ApplyStatus.failed
    assert job.status == JobStatus.failed
    assert job.error is not None
    assert "NoHandlerError" in job.error


async def test_process_job_handler_failure_keeps_active_version(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await enqueue_job(
        name="processor-fail",
        cidr_or_ip="203.0.113.43/32",
        active_version=0,
    )

    await process_job(job_id, session_factory=committed_db, applier=FailingApplier())

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert service.apply_status == ApplyStatus.failed
    assert service.active_version == 0
    assert job.status == JobStatus.failed
    assert job.error is not None
    assert "RuntimeError: apply failed" in job.error


async def test_retry_after_handler_failure_reaches_active(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await enqueue_job(
        name="processor-retry",
        cidr_or_ip="203.0.113.44/32",
    )
    await process_job(job_id, session_factory=committed_db, applier=FailingApplier())
    async with session_scope() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        retried_job = await retry(db, service, actor=None)

    await process_job(retried_job.id, session_factory=committed_db, applier=RecordingApplier())

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded
    assert job.attempts == 2


async def test_reconcile_once_processes_committed_undispatched_job(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await seed_queued_job_without_dispatch(
        committed_db,
        name="processor-reconcile",
        cidr_or_ip="203.0.113.45/32",
        version=1,
    )

    count = await reconcile_once(
        session_factory=committed_db,
        applier=RecordingApplier(),
        include_orphans=False,
    )

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert count == 1
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded


async def test_maintenance_holds_service_update_until_reconciliation(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope() as db:
        await set_maintenance(db, actor=None, enabled=True, ip=None)
    service_id, job_id = await enqueue_job(
        name="processor-maintenance-hold",
        cidr_or_ip="203.0.113.52/32",
    )
    applier = RecordingApplier()

    await process_job(job_id, session_factory=committed_db, applier=applier)

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert service.apply_status == ApplyStatus.queued
    assert service.active_version is None
    assert job.status == JobStatus.queued
    assert job.attempts == 0
    assert applier.versions == []

    async with session_scope() as db:
        await set_maintenance(db, actor=None, enabled=False, ip=None)
    count = await reconcile_once(
        session_factory=committed_db,
        applier=applier,
        include_orphans=False,
    )

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert count == 1
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded
    assert applier.versions == [1]


async def test_maintenance_release_applies_latest_held_service_update(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope() as db:
        await set_maintenance(db, actor=None, enabled=True, ip=None)
    service_id, stale_job_id = await enqueue_job(
        name="processor-maintenance-superseded",
        cidr_or_ip="203.0.113.53/32",
    )
    async with session_scope() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        await bump_version(db, service_id)
        current_job = await enqueue_service_update(
            db,
            service,
            actor=None,
            trigger=ChangeTrigger.rule,
        )
    applier = RecordingApplier()

    held_count = await reconcile_once(
        session_factory=committed_db,
        applier=applier,
        include_orphans=False,
    )

    service, stale_job = await get_service_and_job(committed_db, service_id, stale_job_id)
    _, current_job_state = await get_service_and_job(committed_db, service_id, current_job.id)
    assert held_count == 2
    assert service.apply_status == ApplyStatus.queued
    assert stale_job.status == JobStatus.queued
    assert current_job_state.status == JobStatus.queued
    assert applier.versions == []

    async with session_scope() as db:
        await set_maintenance(db, actor=None, enabled=False, ip=None)
    released_count = await reconcile_once(
        session_factory=committed_db,
        applier=applier,
        include_orphans=False,
    )

    service, stale_job = await get_service_and_job(committed_db, service_id, stale_job_id)
    _, current_job_state = await get_service_and_job(committed_db, service_id, current_job.id)
    assert released_count == 2
    assert service.apply_status == ApplyStatus.active
    assert service.active_version == 2
    assert stale_job.status == JobStatus.superseded
    assert current_job_state.status == JobStatus.succeeded
    assert applier.versions == [2]


async def test_reconcile_once_recovers_orphan_with_system_retry_audit(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    service_id, job_id = await seed_queued_job_without_dispatch(
        committed_db,
        name="processor-orphan",
        cidr_or_ip="203.0.113.46/32",
        version=1,
    )
    async with session_scope() as db:
        await mark_applying(db, job_id)

    count = await reconcile_once(
        session_factory=committed_db,
        applier=RecordingApplier(),
        include_orphans=True,
    )

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    async with committed_db() as db:
        audit = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "apply.retry"))
        ).scalar_one()
    assert count == 1
    assert service.apply_status == ApplyStatus.queued
    assert job.status == JobStatus.queued
    assert job.attempts == 1
    assert audit.actor_username == "system"
    assert await redis_client.lrange(APPLY_QUEUE_KEY, 0, -1) == [str(job_id)]


async def test_recovered_orphan_job_reaches_active(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await seed_queued_job_without_dispatch(
        committed_db,
        name="processor-orphan-active",
        cidr_or_ip="203.0.113.47/32",
        version=1,
    )
    async with session_scope() as db:
        await mark_applying(db, job_id)
    await reconcile_once(
        session_factory=committed_db,
        applier=RecordingApplier(),
        include_orphans=True,
    )

    await process_job(job_id, session_factory=committed_db, applier=RecordingApplier())

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert service.active_version == 1
    assert job.status == JobStatus.succeeded
    assert job.attempts == 2


async def test_process_job_supersedes_mid_apply_when_newer_version_commits(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, first_job_id = await enqueue_job(
        name="processor-churn",
        cidr_or_ip="203.0.113.48/32",
    )
    applier = BarrierApplier()
    first = asyncio.create_task(
        process_job(first_job_id, session_factory=committed_db, applier=applier)
    )
    await asyncio.wait_for(applier.entered.wait(), timeout=2)
    async with session_scope() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        await bump_version(db, service_id)
        second_job = await enqueue_service_update(
            db,
            service,
            actor=None,
            trigger=ChangeTrigger.rule,
        )
    applier.release.set()
    await asyncio.wait_for(first, timeout=2)

    await process_job(second_job.id, session_factory=committed_db, applier=applier)

    service, first_job = await get_service_and_job(committed_db, service_id, first_job_id)
    _, second_job_state = await get_service_and_job(committed_db, service_id, second_job.id)
    assert first_job.status == JobStatus.superseded
    assert second_job_state.status == JobStatus.succeeded
    assert service.active_version == 2
    assert applier.versions == [1, 2]


async def test_duplicate_process_job_is_a_no_op_after_first_delivery(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await enqueue_job(
        name="processor-duplicate",
        cidr_or_ip="203.0.113.49/32",
    )
    applier = RecordingApplier()

    await process_job(job_id, session_factory=committed_db, applier=applier)
    await process_job(job_id, session_factory=committed_db, applier=applier)

    service, job = await get_service_and_job(committed_db, service_id, job_id)
    assert service.active_version == 1
    assert job.attempts == 1
    assert applier.versions == [1]


async def test_reconcile_once_processes_queued_jobs_by_created_at_and_id(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    first_service_id, first_job_id = await seed_queued_job_without_dispatch(
        committed_db,
        name="processor-reconcile-order-newer",
        cidr_or_ip="203.0.113.50/32",
        version=2,
    )
    second_service_id, second_job_id = await seed_queued_job_without_dispatch(
        committed_db,
        name="processor-reconcile-order-older",
        cidr_or_ip="203.0.113.51/32",
        version=1,
    )
    applier = RecordingApplier()

    count = await reconcile_once(
        session_factory=committed_db,
        applier=applier,
        include_orphans=False,
    )

    first_service, first_job = await get_service_and_job(
        committed_db,
        first_service_id,
        first_job_id,
    )
    second_service, second_job = await get_service_and_job(
        committed_db,
        second_service_id,
        second_job_id,
    )
    assert count == 2
    assert first_service.active_version == 2
    assert first_job.status == JobStatus.succeeded
    assert second_service.active_version == 1
    assert second_job.status == JobStatus.succeeded
    assert applier.versions == [2, 1]


async def test_process_job_triggers_nexthop_resolve_on_enabled_service_apply(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await enqueue_job(
        name="nexthop-trigger-resolve",
        cidr_or_ip="203.0.113.80/32",
        version=1,
    )
    async with committed_db() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        service.enabled = True
        await db.commit()

    resolve_calls: list[tuple[int, str]] = []
    evict_calls: list[int] = []

    class FakeNexthop:
        def request_resolve(self, dp_id: int, ip: str) -> None:
            resolve_calls.append((dp_id, ip))

        def request_evict(self, dp_id: int) -> None:
            evict_calls.append(dp_id)

    configure_nexthop_resolver(FakeNexthop())
    try:
        applier = RecordingApplier()
        await process_job(job_id, session_factory=committed_db, applier=applier)
        async with committed_db() as db:
            service = await db.get(ProtectedService, service_id)
            assert service is not None
            dp_id = service.dp_id
        assert resolve_calls == [(dp_id, "203.0.113.80")]
        assert evict_calls == []
    finally:
        configure_nexthop_resolver(None)


async def test_process_job_triggers_nexthop_evict_on_disabled_service_apply(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    service_id, job_id = await enqueue_job(
        name="nexthop-trigger-evict",
        cidr_or_ip="203.0.113.81/32",
        version=1,
    )
    async with committed_db() as db:
        service = await db.get(ProtectedService, service_id)
        assert service is not None
        service.enabled = False
        await db.commit()

    resolve_calls: list[tuple[int, str]] = []
    evict_calls: list[int] = []

    class FakeNexthop:
        def request_resolve(self, dp_id: int, ip: str) -> None:
            resolve_calls.append((dp_id, ip))

        def request_evict(self, dp_id: int) -> None:
            evict_calls.append(dp_id)

    configure_nexthop_resolver(FakeNexthop())
    try:
        applier = RecordingApplier()
        await process_job(job_id, session_factory=committed_db, applier=applier)
        async with committed_db() as db:
            service = await db.get(ProtectedService, service_id)
            assert service is not None
            dp_id = service.dp_id
        assert resolve_calls == []
        assert evict_calls == [dp_id]
    finally:
        configure_nexthop_resolver(None)
