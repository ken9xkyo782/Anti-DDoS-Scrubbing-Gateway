import asyncio

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AgentJob,
    ApplyStatus,
    ChangeTrigger,
    NodeControl,
    ProtectedService,
    Tenant,
)
from app.services.apply import APPLY_QUEUE_KEY
from app.services.node_control import get_node_control
from app.worker.applier import ServiceConfig
from app.worker.node_control_reconciler import FakeBypassWriter, NodeControlReconciler
from app.worker.worker import Worker

pytestmark = pytest.mark.integration


class BarrierApplier:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def apply(self, config: ServiceConfig) -> None:
        del config
        self.entered.set()
        await self.release.wait()


def runtime_settings(**values: object) -> Settings:
    return Settings(
        worker_poll_timeout_seconds=0.01,
        worker_reconcile_interval_seconds=0.01,
        worker_backoff_initial_seconds=0.01,
        worker_backoff_max_seconds=0.05,
        worker_shutdown_grace_seconds=0.01,
        worker_node_control_interval_seconds=0.01,
        **values,
    )


async def set_node_control(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    bypass_enabled: bool | None = None,
    maintenance_enabled: bool | None = None,
) -> None:
    async with session_factory() as db:
        control = await get_node_control(db)
        if bypass_enabled is not None:
            control.bypass_enabled = bypass_enabled
        if maintenance_enabled is not None:
            control.maintenance_enabled = maintenance_enabled
        await db.commit()


async def enqueue_blocked_apply(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> None:
    async with session_factory() as db:
        tenant = Tenant(name="node-control-reconciler-tenant")
        service = ProtectedService(
            tenant=tenant,
            name="node-control-reconciler-service",
            cidr_or_ip="203.0.113.190/32",
            apply_status=ApplyStatus.queued,
            version=1,
        )
        db.add(service)
        await db.flush()
        job = AgentJob(
            target_type="service",
            target_id=service.id,
            version=1,
            trigger=ChangeTrigger.service,
        )
        db.add(job)
        await db.commit()

    await redis.lpush(APPLY_QUEUE_KEY, str(job.id))


@pytest.fixture(autouse=True)
async def reset_node_control(committed_db: async_sessionmaker[AsyncSession]) -> None:
    async with committed_db() as db:
        await db.execute(delete(NodeControl))
        await db.commit()
    yield
    async with committed_db() as db:
        await db.execute(delete(NodeControl))
        await db.commit()


async def test_reconcile_asserts_bypass_on_and_off_from_persisted_state(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    await set_node_control(committed_db, bypass_enabled=True)
    writer = FakeBypassWriter()
    reconciler = NodeControlReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1,
    )

    await reconciler.reconcile_once()
    await set_node_control(committed_db, bypass_enabled=False)
    await reconciler.reconcile_once()

    assert writer.values == [1, 0]
    assert reconciler.asserted_bypass == 0


async def test_reconciler_restart_reasserts_persisted_bypass(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    await set_node_control(committed_db, bypass_enabled=True)
    first_writer = FakeBypassWriter()
    await NodeControlReconciler(
        session_factory=committed_db,
        writer=first_writer,
        interval_seconds=1,
    ).reconcile_once()
    restarted_writer = FakeBypassWriter()

    await NodeControlReconciler(
        session_factory=committed_db,
        writer=restarted_writer,
        interval_seconds=1,
    ).reconcile_once()

    assert first_writer.values == [1]
    assert restarted_writer.values == [1]


async def test_failed_bypass_assertion_remains_unknown_and_retries(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    await set_node_control(committed_db, bypass_enabled=True)
    writer = FakeBypassWriter(results=[False, True])
    reconciler = NodeControlReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1,
    )

    await reconciler.reconcile_once()
    assert reconciler.asserted_bypass is None

    await reconciler.reconcile_once()

    assert writer.values == [1, 1]
    assert reconciler.asserted_bypass == 1


async def test_maintenance_clear_kicks_reconciliation(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    await set_node_control(committed_db, maintenance_enabled=True)
    kicked = asyncio.Event()
    reconciler = NodeControlReconciler(
        session_factory=committed_db,
        writer=FakeBypassWriter(),
        interval_seconds=1,
        on_maintenance_cleared=kicked.set,
    )

    await reconciler.reconcile_once()
    await set_node_control(committed_db, maintenance_enabled=False)
    await reconciler.reconcile_once()

    assert kicked.is_set()


async def test_worker_asserts_bypass_while_an_apply_is_blocked(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop() -> None:
        return None

    monkeypatch.setattr("app.worker.worker.close_redis_client", noop)
    monkeypatch.setattr("app.worker.worker.dispose_engine", noop)
    await set_node_control(committed_db, bypass_enabled=False)
    await enqueue_blocked_apply(committed_db, redis_client)
    writer = FakeBypassWriter()
    reconciler = NodeControlReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=0.01,
    )
    applier = BarrierApplier()
    stop = asyncio.Event()
    worker = Worker(
        settings=runtime_settings(),
        redis=redis_client,
        session_factory=committed_db,
        applier=applier,
        node_control=reconciler,
    )
    task = asyncio.create_task(worker.run(stop))

    try:
        await asyncio.wait_for(applier.entered.wait(), timeout=2)
        await set_node_control(committed_db, bypass_enabled=True)

        for _ in range(200):
            if 1 in writer.values:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Node-control lane did not assert bypass")

        assert not applier.release.is_set()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)
