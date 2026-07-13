from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter, deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
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
from app.worker.telemetry_reader import DropEvent, ServiceCounters, TelemetrySnapshot

logger = logging.getLogger(__name__)


class SnapshotReader(Protocol):
    async def snapshot(self) -> TelemetrySnapshot | None: ...

    def tail(self) -> AsyncIterator[DropEvent]: ...


@dataclass(frozen=True, slots=True)
class _SampledEvent:
    captured_at: datetime
    event: DropEvent


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
        top_talkers_window_seconds: int = 60,
        top_talkers_limit: int = 10,
    ) -> None:
        self.reader = reader
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self.retention_seconds = retention_seconds
        self.node_clean_capacity_bps = int(node_clean_capacity_gbps * Decimal(1_000_000_000))
        self.top_talkers_window_seconds = top_talkers_window_seconds
        self.top_talkers_limit = top_talkers_limit
        self._previous: TelemetrySnapshot | None = None
        self._sampled_events: deque[_SampledEvent] = deque()

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
        sample_task = asyncio.create_task(self._run_sampled_event_lane(stop))
        try:
            while not stop.is_set():
                try:
                    await self.aggregate_once()
                except Exception:
                    logger.exception("Telemetry aggregation iteration failed")

                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
                except TimeoutError:
                    continue
        finally:
            sample_task.cancel()
            await asyncio.gather(sample_task, return_exceptions=True)

    async def _run_sampled_event_lane(self, stop: asyncio.Event) -> None:
        """Keep a single dpstat tail process consuming the shared ring buffer."""
        while not stop.is_set():
            try:
                await self.collect_sampled_events(stop)
            except Exception:
                logger.exception("Sampled top-talker event lane failed")

            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def collect_sampled_events(self, stop: asyncio.Event | None = None) -> None:
        """Collect one streaming reader lifetime into the rolling sampled window."""
        async for event in self.reader.tail():
            captured_at = utc_now()
            self._sampled_events.append(_SampledEvent(captured_at=captured_at, event=event))
            # The tail can remain healthy while snapshots or Postgres are not.
            # Retain only the configured rolling horizon in that failure mode too.
            self._prune_sampled_events(captured_at)
            if stop is not None and stop.is_set():
                return

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
            self._prune_sampled_events(captured_at)
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
                top_dst_ports, top_src = self._top_talkers(service.dp_id)
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
                        top_dst_ports=top_dst_ports,
                        top_src=top_src,
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
            node_top_dst_ports, node_top_src = self._top_talkers(None)
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
                    top_dst_ports=node_top_dst_ports,
                    top_src=node_top_src,
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
        top_dst_ports: list[dict[str, int]],
        top_src: list[dict[str, int | str]],
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
            top_dst_ports=top_dst_ports,
            top_src=top_src,
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

    def _prune_sampled_events(self, captured_at: datetime) -> None:
        cutoff = captured_at - timedelta(seconds=self.top_talkers_window_seconds)
        while self._sampled_events and self._sampled_events[0].captured_at < cutoff:
            self._sampled_events.popleft()

    def _top_talkers(
        self, dp_id: int | None
    ) -> tuple[list[dict[str, int]], list[dict[str, int | str]]]:
        events = (
            sample.event
            for sample in self._sampled_events
            if dp_id is None or sample.event.service_id == dp_id
        )
        dst_ports: Counter[int] = Counter()
        source_ips: Counter[str] = Counter()
        for event in events:
            dst_ports[event.dport] += 1
            source_ips[event.src_ip] += 1
        return (
            [
                {"port": port, "count": count}
                for port, count in sorted(dst_ports.items(), key=lambda item: (-item[1], item[0]))[
                    : self.top_talkers_limit
                ]
            ],
            [
                {"ip": ip, "count": count}
                for ip, count in sorted(source_ips.items(), key=lambda item: (-item[1], item[0]))[
                    : self.top_talkers_limit
                ]
            ],
        )

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
