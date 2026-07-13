import asyncio
import uuid

import pytest

from app.db.models import AgentJob, ChangeTrigger, JobType, ThreatFeedSource
from app.worker.feed_coordinator import FeedCoordinator

pytestmark = pytest.mark.integration


def feed_job() -> AgentJob:
    return AgentJob(
        id=uuid.uuid4(),
        target_type="feed_sync_run",
        feed_sync_run_id=uuid.uuid4(),
        version=1,
        job_type=JobType.feed_sync,
        trigger=ChangeTrigger.feed_manual,
    )


def feed_source() -> ThreatFeedSource:
    return ThreatFeedSource(
        id=uuid.uuid4(),
        name="Coordinator Feed",
        url="https://feeds.example.test/deny.txt",
        sync_interval_seconds=300,
    )


async def test_coordinator_runs_only_one_network_fetch_and_queues_its_completion() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fetch(source: ThreatFeedSource) -> bytes:
        del source
        entered.set()
        await release.wait()
        return b"198.51.100.10\n"

    coordinator = FeedCoordinator(fetch=fetch)
    first = feed_job()

    assert coordinator.start(first, feed_source())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert not coordinator.start(feed_job(), feed_source())

    release.set()
    completion = await asyncio.wait_for(coordinator.next_completion(), timeout=1)

    assert completion.job.id == first.id
    assert completion.body == b"198.51.100.10\n"
    assert completion.error is None


async def test_coordinator_holds_the_slot_until_foreground_completion_is_released() -> None:
    async def fetch(source: ThreatFeedSource) -> bytes:
        del source
        return b"198.51.100.11\n"

    coordinator = FeedCoordinator(fetch=fetch)
    assert coordinator.start(feed_job(), feed_source())
    completion = await asyncio.wait_for(coordinator.next_completion(), timeout=1)

    assert coordinator.busy
    assert not coordinator.start(feed_job(), feed_source())
    coordinator.release(completion)
    assert not coordinator.busy
    assert coordinator.start(feed_job(), feed_source())


async def test_coordinator_reports_fetch_failures_to_the_foreground_lane() -> None:
    async def fetch(source: ThreatFeedSource) -> bytes:
        del source
        raise RuntimeError("upstream unavailable")

    coordinator = FeedCoordinator(fetch=fetch)
    job = feed_job()
    assert coordinator.start(job, feed_source())

    completion = await asyncio.wait_for(coordinator.next_completion(), timeout=1)

    assert completion.job.id == job.id
    assert completion.body is None
    assert isinstance(completion.error, RuntimeError)


async def test_coordinator_cancellation_does_not_publish_a_terminal_completion() -> None:
    entered = asyncio.Event()
    never = asyncio.Event()

    async def fetch(source: ThreatFeedSource) -> bytes:
        del source
        entered.set()
        await never.wait()
        return b"unreachable"

    coordinator = FeedCoordinator(fetch=fetch)
    assert coordinator.start(feed_job(), feed_source())
    await asyncio.wait_for(entered.wait(), timeout=1)

    coordinator.fetch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coordinator.fetch_task

    assert coordinator.completion_queue.empty()
