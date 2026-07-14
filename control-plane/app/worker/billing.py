from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BillingSample,
    BillingStatus,
    BillingUsage,
    OveragePolicy,
    ProtectedService,
    ServicePlan,
    utc_now,
)
from app.services.billing_metrics import bps_to_gbps, p95_nearest_rank
from app.services.billing_period import month_period
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
        interval_seconds: float = 300.0,
        sample_retention_days: int = 400,
        billing_period: str = "monthly",
    ) -> None:
        if billing_period != "monthly":
            raise ValueError("Only monthly billing periods are supported")
        self.reader = reader
        self.session_factory = session_factory
        self._now = now
        self.interval_seconds = interval_seconds
        self.sample_retention_days = sample_retention_days
        self._previous: dict[int, int] = {}
        self._previous_ts_ns: int | None = None
        self._previous_version: int | None = None
        self._services: dict[int, _ServiceCacheEntry] = {}

    async def run_loop(self, stop: asyncio.Event) -> None:
        """Run billing metering until stopped, continuing after a failed iteration."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("Billing metering iteration failed")

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def tick(self) -> None:
        """Run one independently committed sample, rollup, finalization, and prune cycle."""
        await self.sample_once()
        await self.refresh_open_periods()
        await self.finalize_due_periods()
        await self.prune_samples()

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

    async def refresh_open_periods(self) -> None:
        """Refresh the current month's provisional usage for every active service."""
        now = self._now()
        period_start, period_end = month_period(now)

        try:
            async with self.session_factory() as db:
                await self._refresh_service_cache(db)

                for cached_service in self._services.values():
                    samples = list(
                        (
                            await db.scalars(
                                select(BillingSample.clean_bps).where(
                                    BillingSample.service_id == cached_service.service.id,
                                    BillingSample.sample_ts >= period_start,
                                    BillingSample.sample_ts < period_end,
                                )
                            )
                        ).all()
                    )
                    plan = cached_service.plan
                    committed_clean_gbps = (
                        plan.committed_clean_gbps if plan is not None else Decimal("0")
                    )
                    billing_metric = plan.billing_metric if plan is not None else "p95_clean_bps"
                    overage_policy = (
                        plan.overage_policy if plan is not None else OveragePolicy.billed
                    )
                    p95_clean_gbps = bps_to_gbps(p95_nearest_rank(samples))
                    billed_gbps = max(committed_clean_gbps, p95_clean_gbps)
                    overage_gbps = max(Decimal("0"), p95_clean_gbps - committed_clean_gbps)

                    usage = await db.scalar(
                        select(BillingUsage).where(
                            BillingUsage.service_id == cached_service.service.id,
                            BillingUsage.period_start == period_start,
                        )
                    )
                    if usage is None:
                        db.add(
                            BillingUsage(
                                service_id=cached_service.service.id,
                                tenant_id=cached_service.tenant_id,
                                service_name=cached_service.service.name,
                                period_start=period_start,
                                period_end=period_end,
                                billing_metric=billing_metric,
                                committed_clean_gbps=committed_clean_gbps,
                                p95_clean_gbps=p95_clean_gbps,
                                billed_gbps=billed_gbps,
                                overage_gbps=overage_gbps,
                                overage_policy=overage_policy,
                                sample_count=len(samples),
                                status=BillingStatus.open,
                            )
                        )
                    elif usage.status == BillingStatus.open:
                        usage.tenant_id = cached_service.tenant_id
                        usage.service_name = cached_service.service.name
                        usage.period_end = period_end
                        usage.billing_metric = billing_metric
                        usage.committed_clean_gbps = committed_clean_gbps
                        usage.p95_clean_gbps = p95_clean_gbps
                        usage.billed_gbps = billed_gbps
                        usage.overage_gbps = overage_gbps
                        usage.overage_policy = overage_policy
                        usage.sample_count = len(samples)

                await db.commit()
        except SQLAlchemyError:
            logger.exception("Billing usage refresh database operation failed")

    async def finalize_due_periods(self) -> None:
        """Finalize due or orphaned provisional usage without changing prior final rows."""
        now = self._now()

        try:
            async with self.session_factory() as db:
                await db.execute(
                    update(BillingUsage)
                    .where(
                        BillingUsage.status == BillingStatus.open,
                        or_(
                            BillingUsage.period_end <= now,
                            BillingUsage.service_id.is_(None),
                        ),
                    )
                    .values(status=BillingStatus.final, finalized_at=now)
                )
                await db.commit()
        except SQLAlchemyError:
            logger.exception("Billing usage finalization database operation failed")

    async def prune_samples(self) -> None:
        """Discard samples only after their corresponding finalized period has expired."""
        cutoff = self._now() - timedelta(days=self.sample_retention_days)
        finalized_period = (
            select(BillingUsage.id)
            .where(
                BillingUsage.service_id == BillingSample.service_id,
                BillingUsage.status == BillingStatus.final,
                BillingUsage.period_end <= cutoff,
                BillingSample.sample_ts >= BillingUsage.period_start,
                BillingSample.sample_ts < BillingUsage.period_end,
            )
            .exists()
        )

        try:
            async with self.session_factory() as db:
                await db.execute(delete(BillingSample).where(finalized_period))
                await db.commit()
        except SQLAlchemyError:
            logger.exception("Billing sample pruning database operation failed")

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
