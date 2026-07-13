from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    NodeHealthSnapshot,
    ProtectedService,
    TelemetryCounter,
    TelemetryScope,
    XdpMode,
    utc_now,
)
from app.worker.telemetry_reader import ServiceCounters, TelemetrySnapshot

logger = logging.getLogger(__name__)


class SnapshotReader(Protocol):
    async def snapshot(self) -> TelemetrySnapshot | None: ...


class TelemetryAggregator:
    """Persist windowed dataplane telemetry without participating in the job queue."""

    def __init__(
        self,
        *,
        reader: SnapshotReader,
        session_factory: async_sessionmaker[AsyncSession] | None,
        interval_seconds: int,
        retention_seconds: int,
        node_clean_capacity_gbps: Decimal,
    ) -> None:
        self.reader = reader
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self.retention_seconds = retention_seconds
        self.node_clean_capacity_bps = int(node_clean_capacity_gbps * Decimal(1_000_000_000))
        self._previous: TelemetrySnapshot | None = None

    @staticmethod
    def counter_delta(current: int, previous: int, *, reset: bool) -> int:
        """Return an unsigned counter delta, recovering from counter resets."""
        if reset or current < previous:
            return current
        return current - previous

    async def aggregate_once(self) -> None:
        """Read and persist one telemetry window, retaining the last good baseline."""
        snapshot = await self.reader.snapshot()
        captured_at = utc_now()

        try:
            if snapshot is None:
                await self._persist_offline(captured_at)
                return

            await self._persist_snapshot(snapshot, captured_at)
        except SQLAlchemyError:
            logger.exception("Telemetry aggregation database operation failed")
            return

        self._previous = snapshot

    async def run_loop(self, stop: asyncio.Event) -> None:
        """Run aggregation until cancelled, without letting one bad read stop the lane."""
        while not stop.is_set():
            try:
                await self.aggregate_once()
            except Exception:
                logger.exception("Telemetry aggregation iteration failed")

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def _persist_offline(self, captured_at: datetime) -> None:
        async with self._session()() as db:
            db.add(
                NodeHealthSnapshot(
                    captured_at=captured_at,
                    window_seconds=0,
                    xdp_mode=XdpMode.offline,
                    active_slot=None,
                    map_version=None,
                    map_error_count=0,
                    node_clean_bps=0,
                    node_capacity_bps=self.node_clean_capacity_bps,
                    bloom_stats=None,
                )
            )
            await self._prune(db, captured_at)
            await db.commit()

    async def _persist_snapshot(self, snapshot: TelemetrySnapshot, captured_at: datetime) -> None:
        previous = self._previous
        is_baseline = previous is None
        version_reset = previous is not None and (
            snapshot.active_version != previous.active_version
        )
        previous_services = (
            {service.dp_id: service for service in previous.services}
            if previous is not None
            else {}
        )

        async with self._session()() as db:
            service_ids = {
                service.dp_id: service.id
                for service in (await db.scalars(select(ProtectedService))).all()
            }
            clean_pkts = 0
            clean_bytes = 0
            drop_pkts = 0
            drop_bytes = 0

            for service in snapshot.services:
                delta = self._service_delta(
                    service,
                    previous_services.get(service.dp_id),
                    baseline=is_baseline,
                    reset=version_reset,
                )
                clean_pkts += delta.clean_pkts
                clean_bytes += delta.clean_bytes
                drop_pkts += delta.drop_pkts
                drop_bytes += delta.drop_bytes

                service_id = service_ids.get(service.dp_id)
                if service_id is None:
                    continue
                db.add(
                    self._counter_row(
                        scope=TelemetryScope.service,
                        service_id=service_id,
                        dp_id=service.dp_id,
                        captured_at=captured_at,
                        clean_pkts=delta.clean_pkts,
                        clean_bytes=delta.clean_bytes,
                        drop_pkts=delta.drop_pkts,
                        drop_bytes=delta.drop_bytes,
                        drop_by_reason=delta.drop_by_reason,
                        is_baseline=is_baseline,
                    )
                )

            drop_by_reason = self._mapping_delta(
                snapshot.node.counters,
                previous.node.counters if previous is not None else {},
                baseline=is_baseline,
                reset=version_reset,
            )
            node_drop_pkts = sum(
                value
                for key, value in drop_by_reason.items()
                if key not in {"map_error", "map_errors"}
            )
            # The dataplane exposes per-service byte counters but only node-wide
            # drop reasons. Service totals therefore provide node dropped bytes.
            db.add(
                self._counter_row(
                    scope=TelemetryScope.node,
                    service_id=None,
                    dp_id=None,
                    captured_at=captured_at,
                    clean_pkts=clean_pkts,
                    clean_bytes=clean_bytes,
                    drop_pkts=node_drop_pkts,
                    drop_bytes=drop_bytes,
                    drop_by_reason=drop_by_reason,
                    is_baseline=is_baseline,
                )
            )
            db.add(
                NodeHealthSnapshot(
                    captured_at=captured_at,
                    window_seconds=self.interval_seconds,
                    xdp_mode=self._xdp_mode(snapshot.xdp_mode),
                    active_slot=snapshot.active_slot,
                    map_version=snapshot.active_version,
                    map_error_count=snapshot.node.counters.get("map_error", 0),
                    node_clean_bps=self._rate(clean_bytes, 8),
                    node_capacity_bps=self.node_clean_capacity_bps,
                    bloom_stats=snapshot.node.bloom_stats,
                )
            )
            await self._prune(db, captured_at)
            await db.commit()

    def _counter_row(
        self,
        *,
        scope: TelemetryScope,
        service_id: uuid.UUID | None,
        dp_id: int | None,
        captured_at: datetime,
        clean_pkts: int,
        clean_bytes: int,
        drop_pkts: int,
        drop_bytes: int,
        drop_by_reason: dict[str, int],
        is_baseline: bool,
    ) -> TelemetryCounter:
        return TelemetryCounter(
            scope=scope,
            service_id=service_id,
            dp_id=dp_id,
            window_start=captured_at,
            window_seconds=self.interval_seconds,
            clean_pkts=clean_pkts,
            clean_bytes=clean_bytes,
            drop_pkts=drop_pkts,
            drop_bytes=drop_bytes,
            drop_by_reason=drop_by_reason,
            pps=self._rate(clean_pkts),
            bps=self._rate(clean_bytes, 8),
            top_dst_ports=None,
            top_src=None,
            is_baseline=is_baseline,
        )

    def _service_delta(
        self,
        current: ServiceCounters,
        previous: ServiceCounters | None,
        *,
        baseline: bool,
        reset: bool,
    ) -> ServiceCounters:
        if baseline:
            return ServiceCounters(
                dp_id=current.dp_id,
                clean_pkts=0,
                clean_bytes=0,
                drop_pkts=0,
                drop_bytes=0,
                drop_by_reason={key: 0 for key in current.drop_by_reason},
            )

        prior = previous or ServiceCounters(
            dp_id=current.dp_id,
            clean_pkts=0,
            clean_bytes=0,
            drop_pkts=0,
            drop_bytes=0,
            drop_by_reason={},
        )
        return ServiceCounters(
            dp_id=current.dp_id,
            clean_pkts=self.counter_delta(current.clean_pkts, prior.clean_pkts, reset=reset),
            clean_bytes=self.counter_delta(current.clean_bytes, prior.clean_bytes, reset=reset),
            drop_pkts=self.counter_delta(current.drop_pkts, prior.drop_pkts, reset=reset),
            drop_bytes=self.counter_delta(current.drop_bytes, prior.drop_bytes, reset=reset),
            drop_by_reason=self._mapping_delta(
                current.drop_by_reason,
                prior.drop_by_reason,
                baseline=False,
                reset=reset,
            ),
        )

    @classmethod
    def _mapping_delta(
        cls,
        current: Mapping[str, int],
        previous: Mapping[str, int],
        *,
        baseline: bool,
        reset: bool,
    ) -> dict[str, int]:
        if baseline:
            return {key: 0 for key in current}
        return {
            key: cls.counter_delta(value, previous.get(key, 0), reset=reset)
            for key, value in current.items()
        }

    def _rate(self, value: int, multiplier: int = 1) -> int:
        return value * multiplier // self.interval_seconds

    async def _prune(self, db: AsyncSession, captured_at: datetime) -> None:
        cutoff = captured_at - timedelta(seconds=self.retention_seconds)
        await db.execute(delete(TelemetryCounter).where(TelemetryCounter.window_start < cutoff))
        await db.execute(delete(NodeHealthSnapshot).where(NodeHealthSnapshot.captured_at < cutoff))

    def _session(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is None:
            raise RuntimeError("Telemetry aggregation requires a session factory")
        return self.session_factory

    @staticmethod
    def _xdp_mode(value: str) -> XdpMode:
        try:
            return XdpMode(value)
        except ValueError:
            return XdpMode.unknown
