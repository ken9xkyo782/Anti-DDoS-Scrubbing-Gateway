import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.telemetry import (
    CommittedServiceResponse,
    FeedSourceStatusResponse,
    FeedSyncRunStatusResponse,
    JobBacklogResponse,
    LastApplyResponse,
    NodeHealthResponse,
    TelemetryWindowResponse,
)
from app.core.deps import Principal, get_current_user, load_service_for_principal, require_admin
from app.db.models import (
    AgentJob,
    FeedSyncRun,
    JobStatus,
    JobType,
    NodeHealthSnapshot,
    ServicePlan,
    TelemetryCounter,
    TelemetryScope,
    ThreatFeedSource,
    XdpMode,
)
from app.db.session import get_db

router = APIRouter(tags=["telemetry"])

_STALE_AFTER_SECONDS = 4


@router.get(
    "/services/{service_id}/telemetry",
    response_model=TelemetryWindowResponse,
)
async def get_service_telemetry(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TelemetryWindowResponse:
    await load_service_for_principal(db, service_id, principal)
    counter = (
        await db.scalars(
            select(TelemetryCounter)
            .where(
                TelemetryCounter.scope == TelemetryScope.service,
                TelemetryCounter.service_id == service_id,
                TelemetryCounter.is_baseline.is_(False),
            )
            .order_by(TelemetryCounter.window_start.desc())
            .limit(1)
        )
    ).first()
    plan = await db.scalar(select(ServicePlan).where(ServicePlan.service_id == service_id))
    return _telemetry_response(counter, plan)


@router.get("/node/telemetry", response_model=TelemetryWindowResponse)
async def get_node_telemetry(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TelemetryWindowResponse:
    require_admin(principal)
    counter = (
        await db.scalars(
            select(TelemetryCounter)
            .where(
                TelemetryCounter.scope == TelemetryScope.node,
                TelemetryCounter.is_baseline.is_(False),
            )
            .order_by(TelemetryCounter.window_start.desc())
            .limit(1)
        )
    ).first()
    return _telemetry_response(counter, None)


@router.get("/node/health", response_model=NodeHealthResponse)
async def get_node_health(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NodeHealthResponse:
    require_admin(principal)
    health = (
        await db.scalars(
            select(NodeHealthSnapshot).order_by(NodeHealthSnapshot.captured_at.desc()).limit(1)
        )
    ).first()
    backlog = await _job_backlog(db)
    committed_services = await _committed_services(db)
    last_apply = await _last_apply(db)
    feed_sources = await _feed_sources(db)

    if health is None:
        return NodeHealthResponse(
            has_data=False,
            xdp_mode=XdpMode.offline,
            active_slot=None,
            map_version=None,
            map_error_count=0,
            node_clean_bps=0,
            node_capacity_bps=0,
            window_start=None,
            window_seconds=0,
            stale=True,
            bloom_stats={},
            committed_services=committed_services,
            job_backlog=backlog,
            last_apply=last_apply,
            feed_sources=feed_sources,
        )

    return NodeHealthResponse(
        has_data=True,
        xdp_mode=health.xdp_mode,
        active_slot=health.active_slot,
        map_version=health.map_version,
        map_error_count=health.map_error_count,
        node_clean_bps=health.node_clean_bps,
        node_capacity_bps=health.node_capacity_bps,
        window_start=health.captured_at,
        window_seconds=health.window_seconds,
        stale=_is_stale(health.captured_at),
        bloom_stats=health.bloom_stats or {},
        committed_services=committed_services,
        job_backlog=backlog,
        last_apply=last_apply,
        feed_sources=feed_sources,
    )


def _telemetry_response(
    counter: TelemetryCounter | None,
    plan: ServicePlan | None,
) -> TelemetryWindowResponse:
    committed_clean_bps = _committed_clean_bps(plan)
    if counter is None:
        return TelemetryWindowResponse(
            has_data=False,
            clean_pkts=0,
            clean_bytes=0,
            drop_pkts=0,
            drop_bytes=0,
            drop_by_reason={},
            pps=0,
            bps=0,
            top_dst_ports=[],
            top_src=[],
            committed_clean_bps=committed_clean_bps,
            committed_honored=None,
            window_start=None,
            window_seconds=0,
            stale=True,
        )
    return TelemetryWindowResponse(
        has_data=True,
        clean_pkts=counter.clean_pkts,
        clean_bytes=counter.clean_bytes,
        drop_pkts=counter.drop_pkts,
        drop_bytes=counter.drop_bytes,
        drop_by_reason=counter.drop_by_reason,
        pps=counter.pps,
        bps=counter.bps,
        top_dst_ports=counter.top_dst_ports or [],
        top_src=counter.top_src or [],
        committed_clean_bps=committed_clean_bps,
        committed_honored=counter.bps >= committed_clean_bps if plan is not None else None,
        window_start=counter.window_start,
        window_seconds=counter.window_seconds,
        stale=_is_stale(counter.window_start),
    )


def _committed_clean_bps(plan: ServicePlan | None) -> int:
    if plan is None:
        return 0
    return int(plan.committed_clean_gbps * Decimal("1000000000"))


async def _job_backlog(db: AsyncSession) -> JobBacklogResponse:
    return JobBacklogResponse(
        queued=(
            await db.scalar(select(func.count()).where(AgentJob.status == JobStatus.queued)) or 0
        ),
        applying=(
            await db.scalar(select(func.count()).where(AgentJob.status == JobStatus.applying)) or 0
        ),
    )


async def _last_apply(db: AsyncSession) -> LastApplyResponse | None:
    job = (
        await db.scalars(
            select(AgentJob)
            .where(AgentJob.job_type != JobType.feed_sync)
            .order_by(AgentJob.created_at.desc(), AgentJob.id.desc())
            .limit(1)
        )
    ).first()
    if job is None:
        return None
    return LastApplyResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _committed_services(db: AsyncSession) -> list[CommittedServiceResponse]:
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
    rows = (
        await db.execute(
            select(ServicePlan, TelemetryCounter)
            .outerjoin(
                latest_counters,
                and_(
                    latest_counters.c.service_id == ServicePlan.service_id,
                    latest_counters.c.rank == 1,
                ),
            )
            .outerjoin(TelemetryCounter, TelemetryCounter.id == latest_counters.c.counter_id)
            .order_by(ServicePlan.service_id)
        )
    ).all()
    return [
        CommittedServiceResponse(
            service_id=plan.service_id,
            observed_clean_bps=counter.bps if counter is not None else 0,
            committed_clean_bps=_committed_clean_bps(plan),
            honored=(counter.bps >= _committed_clean_bps(plan)) if counter is not None else None,
            window_start=counter.window_start if counter is not None else None,
        )
        for plan, counter in rows
    ]


async def _feed_sources(db: AsyncSession) -> list[FeedSourceStatusResponse]:
    latest_runs = select(
        FeedSyncRun.feed_source_id.label("feed_source_id"),
        FeedSyncRun.id.label("run_id"),
        func.row_number()
        .over(
            partition_by=FeedSyncRun.feed_source_id,
            order_by=(FeedSyncRun.sequence.desc(), FeedSyncRun.id.desc()),
        )
        .label("rank"),
    ).subquery()
    rows = (
        await db.execute(
            select(ThreatFeedSource, FeedSyncRun)
            .where(ThreatFeedSource.deleted_at.is_(None))
            .outerjoin(
                latest_runs,
                and_(
                    latest_runs.c.feed_source_id == ThreatFeedSource.id,
                    latest_runs.c.rank == 1,
                ),
            )
            .outerjoin(FeedSyncRun, FeedSyncRun.id == latest_runs.c.run_id)
            .order_by(ThreatFeedSource.created_at, ThreatFeedSource.id)
        )
    ).all()
    return [
        FeedSourceStatusResponse(
            id=source.id,
            name=source.name,
            enabled=source.enabled,
            last_status=source.last_status,
            last_error=source.last_error,
            last_sync_at=source.last_sync_at,
            last_run=(
                FeedSyncRunStatusResponse(
                    id=run.id,
                    sequence=run.sequence,
                    status=run.status,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    duration_ms=run.duration_ms,
                    error=run.error,
                    valid=run.valid,
                    added=run.added,
                    removed=run.removed,
                    skipped_invalid=run.skipped_invalid,
                    overlap_count=run.overlap_count,
                )
                if run is not None
                else None
            ),
        )
        for source, run in rows
    ]


def _is_stale(window_start: datetime) -> bool:
    return datetime.now(UTC) - window_start > timedelta(seconds=_STALE_AFTER_SECONDS)
