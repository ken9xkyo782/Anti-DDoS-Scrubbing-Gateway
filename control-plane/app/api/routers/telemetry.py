import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.telemetry import (
    FeedSourceStatusResponse,
    JobBacklogResponse,
    NodeHealthResponse,
    TelemetryWindowResponse,
)
from app.core.deps import Principal, get_current_user, load_service_for_principal, require_admin
from app.db.models import (
    AgentJob,
    JobStatus,
    NodeHealthSnapshot,
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
    return _telemetry_response(counter)


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
    return _telemetry_response(counter)


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
            job_backlog=backlog,
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
        job_backlog=backlog,
        feed_sources=feed_sources,
    )


def _telemetry_response(counter: TelemetryCounter | None) -> TelemetryWindowResponse:
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
        window_start=counter.window_start,
        window_seconds=counter.window_seconds,
        stale=_is_stale(counter.window_start),
    )


async def _job_backlog(db: AsyncSession) -> JobBacklogResponse:
    return JobBacklogResponse(
        queued=(
            await db.scalar(select(func.count()).where(AgentJob.status == JobStatus.queued)) or 0
        ),
        applying=(
            await db.scalar(select(func.count()).where(AgentJob.status == JobStatus.applying)) or 0
        ),
    )


async def _feed_sources(db: AsyncSession) -> list[FeedSourceStatusResponse]:
    sources = (
        await db.scalars(
            select(ThreatFeedSource)
            .where(ThreatFeedSource.deleted_at.is_(None))
            .order_by(ThreatFeedSource.created_at, ThreatFeedSource.id)
        )
    ).all()
    return [
        FeedSourceStatusResponse(
            id=source.id,
            name=source.name,
            enabled=source.enabled,
            last_status=source.last_status,
            last_sync_at=source.last_sync_at,
        )
        for source in sources
    ]


def _is_stale(window_start: datetime) -> bool:
    return datetime.now(UTC) - window_start > timedelta(seconds=_STALE_AFTER_SECONDS)
