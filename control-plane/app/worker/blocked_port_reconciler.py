from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.ddos_amplification import list_blocked_ports

logger = logging.getLogger(__name__)


class BlockedPortsWriter(Protocol):
    async def set(self, ports: frozenset[int]) -> bool: ...


class DpstatBlockedPortsWriter:
    def __init__(self, *, binary: str, timeout_seconds: float) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    async def set(self, ports: frozenset[int]) -> bool:
        cmd = [self.binary, "set-blocked-ports"]
        cmd.extend(str(p) for p in sorted(ports))
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Unable to start dpstat blocked-ports writer")
            return False

        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            logger.warning("Timed out setting data-plane blocked ports")
            return False

        if process.returncode != 0:
            logger.warning(
                "Unable to set data-plane blocked ports",
                extra={"ports": sorted(ports), "stderr": stderr.decode(errors="replace")},
            )
            return False
        return True


@dataclass
class FakeBlockedPortsWriter:
    results: list[bool] = field(default_factory=list)
    values: list[frozenset[int]] = field(default_factory=list)

    async def set(self, ports: frozenset[int]) -> bool:
        self.values.append(ports)
        return self.results.pop(0) if self.results else True


class BlockedPortReconciler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        writer: BlockedPortsWriter,
        interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.writer = writer
        self.interval_seconds = interval_seconds
        self.asserted_ports: frozenset[int] | None = None

    async def reconcile_once(self) -> None:
        async with self.session_factory() as db:
            entries = await list_blocked_ports(db)
            desired_ports = frozenset(e.port for e in entries)

        if self.asserted_ports != desired_ports:
            if await self.writer.set(desired_ports):
                self.asserted_ports = desired_ports

    async def run_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("Blocked-port reconciliation failed")

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
