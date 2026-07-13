import asyncio
import logging
import signal
import uuid
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.redis import close_redis_client, get_redis_client
from app.db.models import AgentJob, JobStatus, JobType, utc_now
from app.db.session import dispose_engine, get_session_factory
from app.services.apply import APPLY_QUEUE_KEY
from app.worker.applier import Applier, PlaceholderApplier
from app.worker.feed_coordinator import FeedCoordinator, FeedFetchCompletion
from app.worker.feed_runner import FeedRunner
from app.worker.feed_scheduler import enqueue_due_feed_syncs
from app.worker.handlers import configure_feed_runner
from app.worker.processor import claim_job, process_job, reconcile_once

logger = logging.getLogger(__name__)


class TelemetryLane(Protocol):
    async def run_loop(self, stop: asyncio.Event) -> None: ...


class Worker:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        redis: Redis | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        applier: Applier | None = None,
        feed_runner: FeedRunner | None = None,
        telemetry: TelemetryLane | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.redis = redis or get_redis_client()
        self.session_factory = session_factory or get_session_factory()
        self.applier = applier or PlaceholderApplier()
        self.feed_runner = feed_runner
        self.telemetry = telemetry

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop_event = stop or asyncio.Event()
        installed_signals = self._install_signal_handlers(stop_event)
        feed_coordinator = (
            FeedCoordinator(fetch=self.feed_runner.fetch_source)
            if self.feed_runner is not None
            else None
        )
        if self.feed_runner is not None:
            configure_feed_runner(self.feed_runner)
        telemetry_task = (
            asyncio.create_task(self.telemetry.run_loop(stop_event))
            if self.telemetry is not None
            else None
        )
        inflight: asyncio.Task[object] | None = None
        backoff: float | None = None
        redis_degraded = False
        next_reconcile = asyncio.get_running_loop().time()

        logger.info(
            "Worker starting",
            extra={
                "queue_key": APPLY_QUEUE_KEY,
                "poll_timeout_seconds": self.settings.worker_poll_timeout_seconds,
                "reconcile_interval_seconds": self.settings.worker_reconcile_interval_seconds,
                "backoff_initial_seconds": self.settings.worker_backoff_initial_seconds,
                "backoff_max_seconds": self.settings.worker_backoff_max_seconds,
                "telemetry_enabled": self.telemetry is not None,
            },
        )

        try:
            while not stop_event.is_set():
                backoff = await self._schedule_due_feeds(
                    stop_event,
                    backoff,
                    "startup feed scheduling",
                )
                inflight = asyncio.create_task(
                    reconcile_once(
                        session_factory=self.session_factory,
                        applier=self.applier,
                        include_orphans=True,
                        exclude_feed_sync=feed_coordinator is not None,
                    )
                )
                try:
                    stopping = await self._stop_or_finish(inflight, stop_event)
                except OperationalError:
                    inflight = None
                    backoff = await self._back_off(stop_event, backoff, "startup reconciliation")
                    continue
                if stopping:
                    return
                inflight = None
                break

            next_reconcile = (
                asyncio.get_running_loop().time() + self.settings.worker_reconcile_interval_seconds
            )
            while not stop_event.is_set():
                if feed_coordinator is not None and not feed_coordinator.busy:
                    started_feed_fetch = await self._start_next_feed_fetch(feed_coordinator)
                    if started_feed_fetch:
                        continue

                completion: FeedFetchCompletion | None = None
                try:
                    if feed_coordinator is not None and feed_coordinator.busy:
                        if feed_coordinator.completion_queue.empty():
                            job_id, completion = await self._wait_for_work(
                                stop_event,
                                feed_coordinator,
                            )
                        else:
                            job_id = None
                            completion = await feed_coordinator.next_completion()
                    else:
                        job_id = await self._wait_for_pop(stop_event)
                except RedisError:
                    if not redis_degraded:
                        logger.warning("Redis unavailable; degrading to ledger reconciliation")
                    redis_degraded = True
                    backoff = await self._schedule_due_feeds(
                        stop_event,
                        backoff,
                        "degraded feed scheduling",
                    )
                    inflight = asyncio.create_task(
                        reconcile_once(
                            session_factory=self.session_factory,
                            applier=self.applier,
                            include_orphans=False,
                            exclude_feed_sync=feed_coordinator is not None,
                        )
                    )
                    try:
                        stopping = await self._stop_or_finish(inflight, stop_event)
                    except OperationalError:
                        inflight = None
                        backoff = await self._back_off(
                            stop_event,
                            backoff,
                            "degraded reconciliation",
                        )
                        continue
                    if stopping:
                        break
                    inflight = None
                    backoff = await self._back_off(stop_event, backoff, "Redis retry")
                    continue

                if stop_event.is_set():
                    break
                if redis_degraded:
                    logger.info("Redis connection resumed")
                    redis_degraded = False
                backoff = None

                if completion is not None:
                    inflight = asyncio.create_task(
                        self._complete_feed_fetch(feed_coordinator, completion)
                    )
                    try:
                        stopping = await self._stop_or_finish(inflight, stop_event)
                    except OperationalError:
                        inflight = None
                        backoff = await self._back_off(
                            stop_event,
                            backoff,
                            "feed completion processing",
                        )
                        continue
                    if stopping:
                        break
                    inflight = None
                    continue

                if job_id is not None:
                    if feed_coordinator is not None and await self._is_feed_sync_job(job_id):
                        if not feed_coordinator.busy:
                            await self._start_feed_fetch(job_id, feed_coordinator)
                        continue
                    inflight = asyncio.create_task(
                        process_job(
                            job_id,
                            session_factory=self.session_factory,
                            applier=self.applier,
                        )
                    )
                    try:
                        stopping = await self._stop_or_finish(inflight, stop_event)
                    except OperationalError:
                        inflight = None
                        backoff = await self._back_off(stop_event, backoff, "job processing")
                        continue
                    if stopping:
                        break
                    inflight = None
                    continue

                if asyncio.get_running_loop().time() < next_reconcile:
                    continue
                backoff = await self._schedule_due_feeds(
                    stop_event,
                    backoff,
                    "periodic feed scheduling",
                )
                inflight = asyncio.create_task(
                    reconcile_once(
                        session_factory=self.session_factory,
                        applier=self.applier,
                        include_orphans=False,
                        exclude_feed_sync=feed_coordinator is not None,
                    )
                )
                try:
                    stopping = await self._stop_or_finish(inflight, stop_event)
                except OperationalError:
                    inflight = None
                    backoff = await self._back_off(stop_event, backoff, "periodic reconciliation")
                    continue
                if stopping:
                    break
                inflight = None
                next_reconcile = asyncio.get_running_loop().time()
                next_reconcile += self.settings.worker_reconcile_interval_seconds
        finally:
            stop_event.set()
            self._remove_signal_handlers(installed_signals)
            await self._finish_inflight(inflight)
            try:
                await self._finish_background_lane(telemetry_task)
                await self._finish_inflight(
                    feed_coordinator.inflight_task if feed_coordinator is not None else None
                )
                if self.feed_runner is not None:
                    await self.feed_runner.client.aclose()
                await close_redis_client()
            finally:
                if self.feed_runner is not None:
                    configure_feed_runner(None)
                await dispose_engine()

    async def _schedule_due_feeds(
        self,
        stop: asyncio.Event,
        current_backoff: float | None,
        operation: str,
    ) -> float | None:
        try:
            await enqueue_due_feed_syncs(self.session_factory, utc_now())
        except OperationalError:
            return await self._back_off(stop, current_backoff, operation)
        return None

    async def _start_next_feed_fetch(self, coordinator: FeedCoordinator) -> bool:
        async with self.session_factory() as db:
            job_id = await db.scalar(
                select(AgentJob.id)
                .where(
                    AgentJob.job_type == JobType.feed_sync,
                    AgentJob.status == JobStatus.queued,
                )
                .order_by(AgentJob.created_at.asc(), AgentJob.id.asc())
                .limit(1)
            )
        if job_id is None:
            return False
        return await self._start_feed_fetch(job_id, coordinator)

    async def _start_feed_fetch(self, job_id: uuid.UUID, coordinator: FeedCoordinator) -> bool:
        if self.feed_runner is None or coordinator.busy:
            return False

        job = await claim_job(job_id)
        if job is None:
            return False
        if job.job_type != JobType.feed_sync:
            raise RuntimeError("feed fetch lane claimed a non-feed job")

        source = await self.feed_runner.load_source_for_fetch(job)
        if source is None:
            await self.feed_runner.finish_missing_feed(job)
            return True
        if not coordinator.start(job, source):
            raise RuntimeError("feed fetch lane became unavailable after claim")
        return True

    async def _complete_feed_fetch(
        self,
        coordinator: FeedCoordinator | None,
        completion: FeedFetchCompletion,
    ) -> None:
        if self.feed_runner is None or coordinator is None:
            raise RuntimeError("feed completion arrived without feed dependencies")
        try:
            await self.feed_runner.complete_feed_fetch(completion)
        finally:
            coordinator.release(completion)

    async def _is_feed_sync_job(self, job_id: uuid.UUID) -> bool:
        async with self.session_factory() as db:
            return (
                await db.scalar(select(AgentJob.job_type).where(AgentJob.id == job_id))
                == JobType.feed_sync
            )

    async def _brpop(self) -> uuid.UUID | None:
        result = await self.redis.brpop(
            APPLY_QUEUE_KEY,
            timeout=self.settings.worker_poll_timeout_seconds,
        )
        if result is None:
            return None

        _, raw_job_id = result
        try:
            job_id = raw_job_id.decode() if isinstance(raw_job_id, bytes) else raw_job_id
            return uuid.UUID(job_id)
        except (AttributeError, TypeError, UnicodeDecodeError, ValueError):
            logger.warning("Skipping invalid apply job id from Redis", extra={"value": raw_job_id})
            return None

    def _next_backoff(self, current: float | None) -> float:
        if current is None:
            return min(
                self.settings.worker_backoff_initial_seconds,
                self.settings.worker_backoff_max_seconds,
            )
        return min(current * 2, self.settings.worker_backoff_max_seconds)

    async def _back_off(
        self,
        stop: asyncio.Event,
        current: float | None,
        operation: str,
    ) -> float:
        delay = self._next_backoff(current)
        logger.warning(
            "Worker backing off after %s",
            operation,
            extra={"backoff_seconds": delay},
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass
        return delay

    async def _wait_for_pop(self, stop: asyncio.Event) -> uuid.UUID | None:
        pop_task = asyncio.create_task(self._brpop())
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {pop_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            if not pop_task.done():
                pop_task.cancel()
                await self._discard_cancelled(pop_task)
            return None

        stop_task.cancel()
        await self._discard_cancelled(stop_task)
        return await pop_task

    async def _wait_for_work(
        self,
        stop: asyncio.Event,
        coordinator: FeedCoordinator,
    ) -> tuple[uuid.UUID | None, FeedFetchCompletion | None]:
        pop_task = asyncio.create_task(self._brpop())
        stop_task = asyncio.create_task(stop.wait())
        completion_task = asyncio.create_task(coordinator.next_completion())
        done, _ = await asyncio.wait(
            {pop_task, stop_task, completion_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            if not pop_task.done():
                pop_task.cancel()
                await self._discard_cancelled(pop_task)
            if not completion_task.done():
                completion_task.cancel()
                await self._discard_cancelled(completion_task)
            return None, None
        if completion_task in done:
            if not pop_task.done():
                pop_task.cancel()
                await self._discard_cancelled(pop_task)
            if not stop_task.done():
                stop_task.cancel()
                await self._discard_cancelled(stop_task)
            return None, await completion_task

        if not stop_task.done():
            stop_task.cancel()
            await self._discard_cancelled(stop_task)
        if not completion_task.done():
            completion_task.cancel()
            await self._discard_cancelled(completion_task)
        return await pop_task, None

    async def _stop_or_finish(self, task: asyncio.Task[object], stop: asyncio.Event) -> bool:
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            stop_task.cancel()
            await self._discard_cancelled(stop_task)
            await task
            return False
        return True

    async def _finish_inflight(self, task: asyncio.Task[object] | None) -> None:
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.settings.worker_shutdown_grace_seconds,
            )
        except TimeoutError:
            logger.warning("Worker shutdown grace elapsed; leaving job applying for recovery")
            task.cancel()
            await self._discard_cancelled(task)

    async def _finish_background_lane(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        if not task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self.settings.worker_shutdown_grace_seconds,
                )
            except TimeoutError:
                logger.warning("Telemetry lane did not stop during worker shutdown")
                task.cancel()
                await self._discard_cancelled(task)
                return
            except Exception:
                logger.exception("Telemetry lane stopped with an error")
                return

        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Telemetry lane stopped with an error")

    @staticmethod
    async def _discard_cancelled(task: asyncio.Task[object]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _install_signal_handlers(stop: asyncio.Event) -> list[signal.Signals]:
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        for current_signal in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(current_signal, stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            installed.append(current_signal)
        return installed

    @staticmethod
    def _remove_signal_handlers(installed_signals: list[signal.Signals]) -> None:
        loop = asyncio.get_running_loop()
        for current_signal in installed_signals:
            try:
                loop.remove_signal_handler(current_signal)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
