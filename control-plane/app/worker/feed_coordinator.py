import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.db.models import AgentJob, ThreatFeedSource

Fetch = Callable[[ThreatFeedSource], Awaitable[bytes]]


@dataclass(frozen=True, slots=True)
class FeedFetchCompletion:
    job: AgentJob
    source_id: uuid.UUID
    started: float
    body: bytes | None
    error: Exception | None


class FeedCoordinator:
    """Run one network-only feed fetch and hand it back to the worker loop."""

    def __init__(self, *, fetch: Fetch) -> None:
        self._fetch = fetch
        self._active_job: AgentJob | None = None
        self._fetch_task: asyncio.Task[None] | None = None
        self._completion_queue: asyncio.Queue[FeedFetchCompletion] = asyncio.Queue()

    @property
    def busy(self) -> bool:
        return self._active_job is not None

    @property
    def fetch_task(self) -> asyncio.Task[None]:
        if self._fetch_task is None:
            raise RuntimeError("feed fetch is not running")
        return self._fetch_task

    @property
    def inflight_task(self) -> asyncio.Task[None] | None:
        return self._fetch_task

    @property
    def completion_queue(self) -> asyncio.Queue[FeedFetchCompletion]:
        return self._completion_queue

    def start(self, job: AgentJob, source: ThreatFeedSource) -> bool:
        if self.busy:
            return False

        self._active_job = job
        self._fetch_task = asyncio.create_task(self._run_fetch(job, source, time.monotonic()))
        return True

    async def next_completion(self) -> FeedFetchCompletion:
        return await self._completion_queue.get()

    def release(self, completion: FeedFetchCompletion) -> None:
        if self._active_job is None or self._active_job.id != completion.job.id:
            raise RuntimeError("feed completion does not own the active fetch slot")
        self._active_job = None
        self._fetch_task = None

    async def _run_fetch(
        self,
        job: AgentJob,
        source: ThreatFeedSource,
        started: float,
    ) -> None:
        try:
            body = await self._fetch(source)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            completion = FeedFetchCompletion(
                job=job,
                source_id=source.id,
                started=started,
                body=None,
                error=exc,
            )
        else:
            completion = FeedFetchCompletion(
                job=job,
                source_id=source.id,
                started=started,
                body=body,
                error=None,
            )
        await self._completion_queue.put(completion)
