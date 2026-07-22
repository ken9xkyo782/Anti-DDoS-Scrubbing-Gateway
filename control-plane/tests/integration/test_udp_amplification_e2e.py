from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routers import ddos
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import AuditEvent, BlockedUdpPort, Role, User
from app.db.session import get_db
from app.services.ddos_amplification import HARDCODED_AMP_PORTS
from app.worker.blocked_port_reconciler import BlockedPortReconciler, FakeBlockedPortsWriter

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(ddos.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "amp-e2e-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def test_udp_amplification_end_to_end_flow(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    writer = FakeBlockedPortsWriter()
    reconciler = BlockedPortReconciler(
        session_factory=session_factory,
        writer=writer,
        interval_seconds=1.0,
    )

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)

        # 1. GET initial amplification config
        get_res1 = await client.get("/ddos/amplification")
        assert get_res1.status_code == 200
        data1 = get_res1.json()
        assert data1["hardcoded_ports"] == list(HARDCODED_AMP_PORTS)
        assert data1["dynamic_ports"] == []

        # 2. Admin POSTs to block port 3702 (WS-Discovery)
        post_res = await client.post(
            "/ddos/amplification/ports",
            json={"port": 3702, "note": "WS-Discovery Amplification Vector"},
        )
        assert post_res.status_code == 201

        # Verify DB row
        port_row = await db_session.scalar(
            select(BlockedUdpPort).where(BlockedUdpPort.port == 3702)
        )
        assert port_row is not None
        assert port_row.note == "WS-Discovery Amplification Vector"

        # Verify audit log event
        audit_add = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.action == "ddos.amp_port.added")
        )
        assert audit_add is not None
        assert audit_add.actor_user_id == admin.id

        await db_session.commit()

        # 3. Worker Reconciler ticks -> converges BPF writer to set containing {3702}
        await reconciler.reconcile_once()
        assert reconciler.asserted_ports == frozenset({3702})
        assert len(writer.values) == 1
        assert writer.values[0] == frozenset({3702})

        # 4. Admin DELETEs port 3702
        del_res = await client.delete("/ddos/amplification/ports/3702")
        assert del_res.status_code == 204

        # Verify DB row removed
        port_row_del = await db_session.scalar(
            select(BlockedUdpPort).where(BlockedUdpPort.port == 3702)
        )
        assert port_row_del is None

        # Verify audit log remove event
        audit_rem = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.action == "ddos.amp_port.removed")
        )
        assert audit_rem is not None

        await db_session.commit()

        # 5. Worker Reconciler ticks again -> converges writer to empty set
        await reconciler.reconcile_once()
        assert reconciler.asserted_ports == frozenset()
        assert len(writer.values) == 2
        assert writer.values[1] == frozenset()
