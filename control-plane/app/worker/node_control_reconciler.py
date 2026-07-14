from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.node_control import get_node_control

logger = logging.getLogger(__name__)


class BypassWriter(Protocol):
    async def set(self, bypass: int) -> bool: ...


class DpstatBypassWriter:
    def __init__(self, *, binary: str, timeout_seconds: float) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    async def set(self, bypass: int) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                "set-bypass",
                str(bypass),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Unable to start dpstat bypass writer")
            return False

        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            logger.warning("Timed out setting data-plane bypass")
            return False

        if process.returncode != 0:
            logger.warning(
                "Unable to set data-plane bypass",
                extra={"bypass": bypass, "stderr": stderr.decode(errors="replace")},
            )
            return False
        return True


@dataclass
class FakeBypassWriter:
    results: list[bool] = field(default_factory=list)
    values: list[int] = field(default_factory=list)

    async def set(self, bypass: int) -> bool:
        self.values.append(bypass)
        return self.results.pop(0) if self.results else True


class NodeControlReconciler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        writer: BypassWriter,
        interval_seconds: float,
        on_maintenance_cleared: Callable[[], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.writer = writer
        self.interval_seconds = interval_seconds
        self.on_maintenance_cleared = on_maintenance_cleared
        self.asserted_bypass: int | None = None
        self._maintenance_active: bool | None = None

    async def reconcile_once(self) -> None:
        async with self.session_factory() as db:
            control = await get_node_control(db)
            desired_bypass = int(control.bypass_enabled)
            maintenance_active = control.maintenance_enabled

        if self.asserted_bypass != desired_bypass:
            if await self.writer.set(desired_bypass):
                self.asserted_bypass = desired_bypass

        if self._maintenance_active is True and not maintenance_active:
            if self.on_maintenance_cleared is not None:
                self.on_maintenance_cleared()
        self._maintenance_active = maintenance_active

    async def run_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("Node-control reconciliation failed")

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
