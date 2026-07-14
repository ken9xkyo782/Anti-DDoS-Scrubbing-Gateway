from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BillingSample, ProtectedService, ServicePlan, utc_now
from app.worker.telemetry_reader import TelemetrySnapshot

_NANOSECONDS_PER_SECOND = 1_000_000_000
logger = logging.getLogger(__name__)


class SnapshotReader(Protocol):
    async def snapshot(self) -> TelemetrySnapshot | None: ...


@dataclass(frozen=True, slots=True)
class _ServiceCacheEntry:
    service: ProtectedService
    tenant_id: uuid.UUID
    plan: ServicePlan | None


class BillingMeter:
    """Persist per-service clean bandwidth samples from cumulative dataplane counters."""

    def __init__(
        self,
        *,
        reader: SnapshotReader,
        session_factory: async_sessionmaker[AsyncSession],
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.reader = reader
        self.session_factory = session_factory
        self._now = now
        self._previous: dict[int, int] = {}
        self._previous_ts_ns: int | None = None
        self._previous_version: int | None = None
        self._services: dict[int, _ServiceCacheEntry] = {}

    async def sample_once(self) -> None:
        """Read one snapshot and persist the elapsed clean-byte rate for each active service."""
        snapshot = await self.reader.snapshot()
        if snapshot is None:
            return

        try:
            async with self.session_factory() as db:
                await self._refresh_service_cache(db)

                if self._previous_ts_ns is None:
                    await db.commit()
                    self._set_previous(snapshot)
                    return

                elapsed_seconds = (snapshot.ts_ns - self._previous_ts_ns) // _NANOSECONDS_PER_SECOND
                if elapsed_seconds <= 0:
                    await db.commit()
                    return

                version_reset = snapshot.active_version != self._previous_version
                sample_ts = self._now()
                observed_dp_ids: set[int] = set()

                for counters in snapshot.services:
                    service = self._services.get(counters.dp_id)
                    if service is None:
                        continue

                    observed_dp_ids.add(counters.dp_id)
                    previous_bytes = self._previous.get(counters.dp_id)
                    is_reset = version_reset or (
                        previous_bytes is not None and counters.clean_bytes < previous_bytes
                    )
                    if is_reset:
                        clean_bytes = counters.clean_bytes
                    elif previous_bytes is None:
                        clean_bytes = 0
                    else:
                        clean_bytes = counters.clean_bytes - previous_bytes

                    await self._upsert_sample(
                        db,
                        service_id=service.service.id,
                        dp_id=counters.dp_id,
                        sample_ts=sample_ts,
                        clean_bps=clean_bytes // elapsed_seconds,
                        window_seconds=elapsed_seconds,
                        is_reset=is_reset,
                    )

                for dp_id, service in self._services.items():
                    if dp_id in observed_dp_ids:
                        continue
                    await self._upsert_sample(
                        db,
                        service_id=service.service.id,
                        dp_id=dp_id,
                        sample_ts=sample_ts,
                        clean_bps=0,
                        window_seconds=elapsed_seconds,
                        is_reset=False,
                    )

                await db.commit()
        except SQLAlchemyError:
            logger.exception("Billing sample database operation failed")
            return

        self._set_previous(snapshot)

    async def _refresh_service_cache(self, db: AsyncSession) -> None:
        rows = (
            await db.execute(select(ProtectedService, ServicePlan).outerjoin(ServicePlan))
        ).all()
        self._services = {
            service.dp_id: _ServiceCacheEntry(
                service=service,
                tenant_id=service.tenant_id,
                plan=plan,
            )
            for service, plan in rows
        }

    async def _upsert_sample(
        self,
        db: AsyncSession,
        *,
        service_id: uuid.UUID,
        dp_id: int,
        sample_ts: datetime,
        clean_bps: int,
        window_seconds: int,
        is_reset: bool,
    ) -> None:
        statement = insert(BillingSample).values(
            service_id=service_id,
            dp_id=dp_id,
            sample_ts=sample_ts,
            clean_bps=clean_bps,
            window_seconds=window_seconds,
            is_reset=is_reset,
        )
        await db.execute(
            statement.on_conflict_do_nothing(index_elements=["service_id", "sample_ts"])
        )

    def _set_previous(self, snapshot: TelemetrySnapshot) -> None:
        self._previous = {counters.dp_id: counters.clean_bytes for counters in snapshot.services}
        self._previous_ts_ns = snapshot.ts_ns
        self._previous_version = snapshot.active_version
