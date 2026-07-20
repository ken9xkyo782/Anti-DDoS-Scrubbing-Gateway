import asyncio

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProtectedService, Tenant
from app.worker.nexthop_resolver import FakeNextHopWriter, NextHopResolver

pytestmark = pytest.mark.integration


async def create_test_service(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    cidr_or_ip: str,
    enabled: bool,
    dp_id: int,
) -> ProtectedService:
    async with session_factory() as db:
        tenant = Tenant(name=f"Nexthop Test Tenant {name}")
        service = ProtectedService(
            tenant=tenant,
            name=name,
            cidr_or_ip=cidr_or_ip,
            enabled=enabled,
            dp_id=dp_id,
            version=1,
        )
        db.add_all([tenant, service])
        await db.commit()
        await db.refresh(service)
        return service


@pytest.fixture(autouse=True)
async def cleanup_db(committed_db: async_sessionmaker[AsyncSession]) -> None:
    yield
    async with committed_db() as db:
        await db.execute(delete(ProtectedService))
        await db.execute(delete(Tenant))
        await db.commit()


async def test_reconcile_resolves_enabled_and_evicts_stale(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    # 1. Active service in DB enabled=True
    await create_test_service(
        committed_db, name="s1", cidr_or_ip="203.0.113.10/32", enabled=True, dp_id=101
    )
    # 2. Disabled service in DB
    await create_test_service(
        committed_db, name="s2", cidr_or_ip="203.0.113.11/32", enabled=False, dp_id=102
    )

    writer = FakeNextHopWriter()
    # Mock that dp_ids 101 and 103 are currently active in BPF map
    # 101 needs resolve (enabled)
    # 103 is stale (deleted/not in DB or disabled) and should be evicted
    writer.active_dp_ids = {101, 103}

    resolver = NextHopResolver(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1.0,
    )

    await resolver.resolve_once()

    # Verify that resolve was called for 101
    assert (101, "203.0.113.10") in writer.resolve_calls
    # Verify that resolve was NOT called for 102 (disabled)
    assert not any(call[0] == 102 for call in writer.resolve_calls)
    # Verify that evict was called for 103 (stale active)
    assert 103 in writer.evict_calls
    # Verify that evict was NOT called for 101 (enabled)
    assert 101 not in writer.evict_calls


async def test_immediate_queue_drain(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    writer = FakeNextHopWriter()
    resolver = NextHopResolver(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=10.0,
    )

    stop = asyncio.Event()
    loop_task = asyncio.create_task(resolver.run_loop(stop))

    try:
        # Enqueue resolve request
        resolver.request_resolve(105, "203.0.113.50")
        # Enqueue evict request
        resolver.request_evict(106)

        # Wait a short moment to allow queue to drain
        for _ in range(50):
            if (105, "203.0.113.50") in writer.resolve_calls and 106 in writer.evict_calls:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Queue did not drain promptly")

    finally:
        stop.set()
        await asyncio.wait_for(loop_task, timeout=2.0)
