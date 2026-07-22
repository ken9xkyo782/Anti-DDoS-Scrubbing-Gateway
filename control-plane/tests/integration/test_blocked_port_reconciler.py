import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Role, User
from app.services.ddos_amplification import add_blocked_port
from app.worker.blocked_port_reconciler import BlockedPortReconciler, FakeBlockedPortsWriter

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession) -> User:
    actor = User(
        username="reconciler-actor",
        role=Role.admin,
        password_hash="$argon2id$hash",
    )
    db_session.add(actor)
    await db_session.flush()
    return actor


async def test_reconcile_once_converges_desired_ports(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        actor = await create_admin(db)
        await add_blocked_port(db, actor, 1000, note="Port 1000")
        await add_blocked_port(db, actor, 2000, note="Port 2000")
        await db.commit()

    writer = FakeBlockedPortsWriter()
    reconciler = BlockedPortReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1.0,
    )

    await reconciler.reconcile_once()

    assert reconciler.asserted_ports == frozenset({1000, 2000})
    assert len(writer.values) == 1
    assert writer.values[0] == frozenset({1000, 2000})


async def test_no_drift_skips_writer_call(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        actor = await create_admin(db)
        await add_blocked_port(db, actor, 3000)
        await db.commit()

    writer = FakeBlockedPortsWriter()
    reconciler = BlockedPortReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1.0,
    )

    await reconciler.reconcile_once()
    assert len(writer.values) == 1

    # Second reconcile without changes -> no additional write
    await reconciler.reconcile_once()
    assert len(writer.values) == 1


async def test_restart_reasserts_once(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        actor = await create_admin(db)
        await add_blocked_port(db, actor, 4000)
        await db.commit()

    writer = FakeBlockedPortsWriter()
    reconciler = BlockedPortReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1.0,
    )

    await reconciler.reconcile_once()
    assert len(writer.values) == 1

    # Simulate restart by clearing in-memory asserted state
    reconciler.asserted_ports = None

    await reconciler.reconcile_once()
    assert len(writer.values) == 2
    assert writer.values[1] == frozenset({4000})


async def test_writer_failure_retains_last_good_and_retries(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        actor = await create_admin(db)
        await add_blocked_port(db, actor, 5000)
        await db.commit()

    writer = FakeBlockedPortsWriter(results=[False, True])
    reconciler = BlockedPortReconciler(
        session_factory=committed_db,
        writer=writer,
        interval_seconds=1.0,
    )

    # First attempt fails -> asserted_ports stays None
    await reconciler.reconcile_once()
    assert reconciler.asserted_ports is None
    assert len(writer.values) == 1

    # Second attempt succeeds -> asserted_ports updated
    await reconciler.reconcile_once()
    assert reconciler.asserted_ports == frozenset({5000})
    assert len(writer.values) == 2
