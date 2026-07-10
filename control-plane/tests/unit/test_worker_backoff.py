import uuid

import pytest

from app.core.config import Settings
from app.services.apply import APPLY_QUEUE_KEY
from app.worker.worker import Worker

pytestmark = pytest.mark.unit


class FakeRedis:
    def __init__(self, responses: list[tuple[str, str] | None]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    async def brpop(self, key: str, **kwargs: float) -> tuple[str, str] | None:
        self.calls.append((key, kwargs["timeout"]))
        return self.responses.pop(0)


def test_backoff_doubles_from_initial_and_caps_at_maximum() -> None:
    settings = Settings(worker_backoff_initial_seconds=0.5, worker_backoff_max_seconds=2.0)
    worker = Worker(settings=settings, redis=FakeRedis([]))

    delays = [worker._next_backoff(delay) for delay in (None, 0.5, 1.0, 2.0)]

    assert delays == [0.5, 1.0, 2.0, 2.0]


async def test_brpop_maps_tuple_to_uuid_and_none_to_none() -> None:
    job_id = uuid.uuid4()
    settings = Settings(worker_poll_timeout_seconds=0.25)
    redis = FakeRedis([(APPLY_QUEUE_KEY, str(job_id)), None])
    worker = Worker(settings=settings, redis=redis)

    assert await worker._brpop() == job_id
    assert await worker._brpop() is None
    assert redis.calls == [(APPLY_QUEUE_KEY, 0.25), (APPLY_QUEUE_KEY, 0.25)]
