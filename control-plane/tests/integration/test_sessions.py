import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from app.core.sessions import RedisSessionStore

pytestmark = pytest.mark.integration


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


async def test_create_get_round_trips_session(redis_client: Redis) -> None:
    user_id = uuid.uuid4()
    store = RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)

    sid = await store.create(user_id=user_id, session_version=3, ip="127.0.0.1")
    session = await store.get(sid)

    assert session is not None
    assert session.sid == sid
    assert session.user_id == user_id
    assert session.session_version == 3
    assert session.ip == "127.0.0.1"


async def test_get_unknown_or_expired_session_returns_none(redis_client: Redis) -> None:
    clock = Clock()
    store = RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=10, clock=clock)
    sid = await store.create(user_id=uuid.uuid4(), session_version=1, ip=None)

    assert await store.get("missing") is None

    clock.advance(11)
    assert await store.get(sid) is None
    assert await redis_client.exists(f"session:{sid}") == 0


async def test_revoke_kills_one_session(redis_client: Redis) -> None:
    user_id = uuid.uuid4()
    store = RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)
    sid = await store.create(user_id=user_id, session_version=1, ip=None)

    await store.revoke(sid)

    assert await store.get(sid) is None
    assert await redis_client.smembers(f"user_sessions:{user_id}") == set()


async def test_revoke_all_kills_all_user_sessions(redis_client: Redis) -> None:
    user_id = uuid.uuid4()
    store = RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)
    first = await store.create(user_id=user_id, session_version=1, ip=None)
    second = await store.create(user_id=user_id, session_version=1, ip=None)

    await store.revoke_all(user_id)

    assert await store.get(first) is None
    assert await store.get(second) is None
    assert await redis_client.smembers(f"user_sessions:{user_id}") == set()


async def test_list_for_user_and_sliding_ttl(redis_client: Redis) -> None:
    user_id = uuid.uuid4()
    store = RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)
    sid = await store.create(user_id=user_id, session_version=1, ip=None)
    await redis_client.expire(f"session:{sid}", 1)

    listed = await store.list_for_user(user_id)
    assert [session.sid for session in listed] == [sid]

    before = await redis_client.ttl(f"session:{sid}")
    assert await store.get(sid) is not None
    after = await redis_client.ttl(f"session:{sid}")

    assert before == 1
    assert after > before
