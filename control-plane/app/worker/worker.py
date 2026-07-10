import asyncio
import logging
import signal
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.redis import close_redis_client, get_redis_client
from app.db.session import dispose_engine, get_session_factory
from app.services.apply import APPLY_QUEUE_KEY
from app.worker.applier import Applier, PlaceholderApplier
from app.worker.processor import process_job, reconcile_once

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        redis: Redis | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        applier: Applier | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.redis = redis or get_redis_client()
        self.session_factory = session_factory or get_session_factory()
        self.applier = applier or PlaceholderApplier()

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop_event = stop or asyncio.Event()
        installed_signals = self._install_signal_handlers(stop_event)
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
            },
        )

        try:
            while not stop_event.is_set():
                inflight = asyncio.create_task(
                    reconcile_once(
                        session_factory=self.session_factory,
                        applier=self.applier,
                        include_orphans=True,
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
                try:
                    job_id = await self._wait_for_pop(stop_event)
                except RedisError:
                    if not redis_degraded:
                        logger.warning("Redis unavailable; degrading to ledger reconciliation")
                    redis_degraded = True
                    inflight = asyncio.create_task(
                        reconcile_once(
                            session_factory=self.session_factory,
                            applier=self.applier,
                            include_orphans=False,
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

                if job_id is not None:
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
                inflight = asyncio.create_task(
                    reconcile_once(
                        session_factory=self.session_factory,
                        applier=self.applier,
                        include_orphans=False,
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
            self._remove_signal_handlers(installed_signals)
            await self._finish_inflight(inflight)
            try:
                await close_redis_client()
            finally:
                await dispose_engine()

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
