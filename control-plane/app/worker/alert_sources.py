"""Persisted source reader for the alert-evaluation worker lane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from app.db.models import (
    AgentJob,
    FeedSyncOverlap,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    NodeControl,
    NodeHealthSnapshot,
    ProtectedService,
    ServicePlan,
    TelemetryCounter,
    TelemetryScope,
)
from app.services.alert_rules import AlertInputs, NodeAlertInputs, ServiceAlertInputs
from app.services.telemetry_math import committed_clean_bps


@dataclass(frozen=True, slots=True)
class AlertSources:
    """Load the persisted alert inputs without issuing per-service queries."""

    telemetry_stale_seconds: float = 60.0
    stuck_applying_seconds: float = 300.0

    async def load(self, db: AsyncSession, now: datetime) -> AlertInputs:
        health = await db.scalar(
            select(NodeHealthSnapshot)
            .order_by(NodeHealthSnapshot.captured_at.desc(), NodeHealthSnapshot.id.desc())
            .limit(1)
        )
        service_rows = await self._service_rows(db)
        jobs = await self._job_inputs(db, now)
        feed_failure_count = await self._feed_failure_count(db)
        overlaps = await self._overlap_counts(db)
        control = await db.get(NodeControl, 1)

        services = tuple(
            ServiceAlertInputs(
                scope_key=str(service.dp_id),
                tenant_id=service.tenant_id,
                service_id=service.id,
                clean_bps=counter.bps if counter is not None else None,
                committed_bps=committed_clean_bps(plan),
                drop_bps=self._drop_bps(counter),
                total_bps=self._total_bps(counter),
                whitelist_overlap_count=overlaps.get(service.id, 0),
            )
            for service, plan, counter in service_rows
        )

        return AlertInputs(
            node=NodeAlertInputs(
                map_error_count=health.map_error_count if health is not None else None,
                xdp_mode=health.xdp_mode.value if health is not None else None,
                node_clean_bps=health.node_clean_bps if health is not None else None,
                node_capacity_bps=health.node_capacity_bps if health is not None else None,
                apply_failed_count=jobs.failed_count,
                job_backlog=jobs.queued_count,
                stuck_applying=jobs.stuck_applying,
                telemetry_stale=self._telemetry_stale(health, now),
                feed_failure_count=feed_failure_count,
                bloom_false_positives=self._bloom_false_positives(health),
                bypass_enabled=control.bypass_enabled if control is not None else None,
                maintenance_enabled=control.maintenance_enabled if control is not None else None,
            ),
            services=services,
        )

    async def _service_rows(
        self,
        db: AsyncSession,
    ) -> list[tuple[ProtectedService, ServicePlan | None, TelemetryCounter | None]]:
        latest_counters = (
            select(
                TelemetryCounter.service_id.label("service_id"),
                TelemetryCounter.id.label("counter_id"),
                func.row_number()
                .over(
                    partition_by=TelemetryCounter.service_id,
                    order_by=(TelemetryCounter.window_start.desc(), TelemetryCounter.id.desc()),
                )
                .label("rank"),
            )
            .where(
                TelemetryCounter.scope == TelemetryScope.service,
                TelemetryCounter.is_baseline.is_(False),
            )
            .subquery()
        )
        rows = await db.execute(
            select(ProtectedService, ServicePlan, TelemetryCounter)
            .outerjoin(ServicePlan, ServicePlan.service_id == ProtectedService.id)
            .outerjoin(
                latest_counters,
                and_(
                    latest_counters.c.service_id == ProtectedService.id,
                    latest_counters.c.rank == 1,
                ),
            )
            .outerjoin(TelemetryCounter, TelemetryCounter.id == latest_counters.c.counter_id)
            .order_by(ProtectedService.dp_id)
        )
        return list(rows.tuples().all())

    async def _job_inputs(self, db: AsyncSession, now: datetime) -> _JobInputs:
        jobs = (
            await db.scalars(
                select(AgentJob).where(
                    AgentJob.status.in_((JobStatus.queued, JobStatus.failed, JobStatus.applying))
                )
            )
        ).all()
        queued_count = sum(job.status is JobStatus.queued for job in jobs)
        failed_count = int(any(job.status is JobStatus.failed for job in jobs))
        applying_started = [
            job.started_at or job.created_at for job in jobs if job.status is JobStatus.applying
        ]
        oldest_applying = min(applying_started, default=None)
        stuck_applying = (
            now - oldest_applying >= timedelta(seconds=self.stuck_applying_seconds)
            if oldest_applying is not None
            else False
        )
        return _JobInputs(
            queued_count=queued_count,
            failed_count=failed_count,
            stuck_applying=stuck_applying,
        )

    async def _feed_failure_count(self, db: AsyncSession) -> int:
        latest_runs = self._latest_feed_runs()
        statuses = (
            await db.scalars(select(latest_runs.c.status).where(latest_runs.c.rank == 1))
        ).all()
        return sum(status is FeedSyncStatus.failed for status in statuses)

    async def _overlap_counts(self, db: AsyncSession) -> dict[object, int]:
        latest_runs = self._latest_feed_runs(successful_only=True)
        rows = await db.execute(
            select(FeedSyncOverlap.service_id, func.count(FeedSyncOverlap.id))
            .join(latest_runs, FeedSyncOverlap.feed_sync_run_id == latest_runs.c.run_id)
            .where(latest_runs.c.rank == 1)
            .group_by(FeedSyncOverlap.service_id)
        )
        return {service_id: count for service_id, count in rows.tuples()}

    @staticmethod
    def _latest_feed_runs(*, successful_only: bool = False) -> Subquery:
        statement = select(
            FeedSyncRun.feed_source_id.label("feed_source_id"),
            FeedSyncRun.id.label("run_id"),
            FeedSyncRun.status.label("status"),
            func.row_number()
            .over(
                partition_by=FeedSyncRun.feed_source_id,
                order_by=(FeedSyncRun.sequence.desc(), FeedSyncRun.id.desc()),
            )
            .label("rank"),
        )
        if successful_only:
            statement = statement.where(
                FeedSyncRun.status.in_((FeedSyncStatus.success, FeedSyncStatus.partial))
            )
        return statement.subquery()

    def _telemetry_stale(self, health: NodeHealthSnapshot | None, now: datetime) -> bool | None:
        if health is None:
            return None
        return now - health.captured_at >= timedelta(seconds=self.telemetry_stale_seconds)

    @staticmethod
    def _bloom_false_positives(health: NodeHealthSnapshot | None) -> int | None:
        if health is None or health.bloom_stats is None:
            return None
        value = health.bloom_stats.get("bloom_hit_lpm_miss")
        return value if isinstance(value, int) else None

    @staticmethod
    def _drop_bps(counter: TelemetryCounter | None) -> int | None:
        if counter is None or counter.window_seconds <= 0:
            return None
        return counter.drop_bytes * 8 // counter.window_seconds

    @classmethod
    def _total_bps(cls, counter: TelemetryCounter | None) -> int | None:
        drop_bps = cls._drop_bps(counter)
        if counter is None or drop_bps is None:
            return None
        return counter.bps + drop_bps


@dataclass(frozen=True, slots=True)
class _JobInputs:
    queued_count: int
    failed_count: int
    stuck_applying: bool
