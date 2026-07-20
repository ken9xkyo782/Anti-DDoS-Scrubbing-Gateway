from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProtectedService

logger = logging.getLogger(__name__)


class NextHopWriter(Protocol):
    async def resolve(self, dp_id: int, ip: str) -> bool: ...
    async def evict(self, dp_id: int) -> bool: ...
    async def get_active_dp_ids(self) -> set[int]: ...


class DpstatNextHopWriter:
    def __init__(self, *, binary: str, timeout_seconds: float) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    async def resolve(self, dp_id: int, ip: str) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                "resolve-nexthop",
                str(dp_id),
                ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Unable to start dpstat nexthop resolver")
            return False

        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            logger.warning("Timed out resolving nexthop for dp_id %d", dp_id)
            return False

        if process.returncode != 0:
            logger.warning(
                "Unable to resolve nexthop",
                extra={"dp_id": dp_id, "ip": ip, "stderr": stderr.decode(errors="replace")},
            )
            return False
        return True

    async def evict(self, dp_id: int) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                "evict-nexthop",
                str(dp_id),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Unable to start dpstat nexthop evictor")
            return False

        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            logger.warning("Timed out evicting nexthop for dp_id %d", dp_id)
            return False

        if process.returncode != 0:
            logger.warning(
                "Unable to evict nexthop",
                extra={"dp_id": dp_id, "stderr": stderr.decode(errors="replace")},
            )
            return False
        return True

    async def get_active_dp_ids(self) -> set[int]:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                "snapshot",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Unable to start dpstat for snapshot in nexthop resolver")
            return set()

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            logger.warning("Timed out getting snapshot in nexthop resolver")
            return set()

        if process.returncode != 0:
            logger.warning(
                "Unable to get snapshot in nexthop resolver",
                extra={"stderr": stderr.decode(errors="replace")},
            )
            return set()

        try:
            import json

            data = json.loads(stdout)
            nexthops = data.get("nexthop", [])
            return {int(nh["dp_id"]) for nh in nexthops}
        except Exception:
            logger.exception("Failed to parse snapshot output in nexthop resolver")
            return set()


@dataclass
class FakeNextHopWriter:
    resolve_results: list[bool] = field(default_factory=list)
    evict_results: list[bool] = field(default_factory=list)
    resolve_calls: list[tuple[int, str]] = field(default_factory=list)
    evict_calls: list[int] = field(default_factory=list)
    active_dp_ids: set[int] = field(default_factory=set)

    async def resolve(self, dp_id: int, ip: str) -> bool:
        self.resolve_calls.append((dp_id, ip))
        return self.resolve_results.pop(0) if self.resolve_results else True

    async def evict(self, dp_id: int) -> bool:
        self.evict_calls.append(dp_id)
        return self.evict_results.pop(0) if self.evict_results else True

    async def get_active_dp_ids(self) -> set[int]:
        return self.active_dp_ids


class NextHopResolver:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        writer: NextHopWriter,
        interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.writer = writer
        self.interval_seconds = interval_seconds
        self._queue: asyncio.Queue[tuple[str, int, str | None]] = asyncio.Queue()

    def request_resolve(self, dp_id: int, ip: str) -> None:
        self._queue.put_nowait(("resolve", dp_id, ip))

    def request_evict(self, dp_id: int) -> None:
        self._queue.put_nowait(("evict", dp_id, None))

    async def resolve_once(self) -> None:
        async with self.session_factory() as db:
            stmt = select(ProtectedService.dp_id, ProtectedService.cidr_or_ip).where(
                ProtectedService.enabled
            )
            res = await db.execute(stmt)
            enabled_services = res.all()

        enabled_dp_ids = set()
        for dp_id, cidr_or_ip in enabled_services:
            enabled_dp_ids.add(dp_id)
            ip_net = ipaddress.ip_network(cidr_or_ip, strict=False)
            ip_str = str(ip_net.network_address)
            await self.writer.resolve(dp_id, ip_str)

        active_dp_ids = await self.writer.get_active_dp_ids()
        for dp_id in active_dp_ids:
            if dp_id not in enabled_dp_ids:
                await self.writer.evict(dp_id)

    async def run_loop(self, stop: asyncio.Event) -> None:
        try:
            await self.resolve_once()
        except Exception:
            logger.exception("Initial nexthop resolution failed")

        queue_task = asyncio.create_task(self._drain_queue(stop))

        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                try:
                    await self.resolve_once()
                except Exception:
                    logger.exception("Periodic nexthop resolution failed")
            else:
                break

        queue_task.cancel()
        try:
            await queue_task
        except asyncio.CancelledError:
            pass

    async def _drain_queue(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                op, dp_id, ip = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                if op == "resolve":
                    assert ip is not None
                    await self.writer.resolve(dp_id, ip)
                elif op == "evict":
                    await self.writer.evict(dp_id)
            except Exception:
                logger.exception("Failed to process queue operation %s for dp_id %d", op, dp_id)
            finally:
                self._queue.task_done()
