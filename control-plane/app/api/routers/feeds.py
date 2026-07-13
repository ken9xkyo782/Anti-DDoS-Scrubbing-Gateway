import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.feeds import (
    FeedSourceCreateRequest,
    FeedSourceResponse,
    FeedSourceUpdateRequest,
    FeedSyncAccepted,
    FeedSyncJobResponse,
    FeedSyncRunResponse,
)
from app.core.deps import Principal, get_current_user, require_admin
from app.db.models import AgentJob, ChangeTrigger, FeedSyncRun, ThreatFeedSource, User
from app.db.session import get_db
from app.services import feeds as feed_service

router = APIRouter(prefix="/feeds", tags=["feeds"])


async def get_admin_principal(
    principal: Annotated[Principal, Depends(get_current_user)],
) -> Principal:
    return require_admin(principal)


@router.post("", response_model=FeedSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_feed(
    payload: FeedSourceCreateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedSourceResponse:
    actor = await _load_actor(db, principal)
    source = await feed_service.create_source(db, payload, actor)
    return _source_response(source)


@router.get("", response_model=list[FeedSourceResponse])
async def list_feeds(
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FeedSourceResponse]:
    sources = list(
        (
            await db.scalars(
                select(ThreatFeedSource)
                .where(ThreatFeedSource.deleted_at.is_(None))
                .order_by(ThreatFeedSource.created_at, ThreatFeedSource.id)
            )
        ).all()
    )
    return [_source_response(source) for source in sources]


@router.get("/{source_id}", response_model=FeedSourceResponse)
async def get_feed(
    source_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedSourceResponse:
    return _source_response(await _active_source(db, source_id))


@router.put("/{source_id}", response_model=FeedSourceResponse)
async def update_feed(
    source_id: uuid.UUID,
    payload: FeedSourceUpdateRequest,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedSourceResponse:
    source = await _active_source(db, source_id)
    actor = await _load_actor(db, principal)
    updated = await feed_service.update_source(db, source, payload, actor)
    return _source_response(updated)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed(
    source_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    source = await _active_source(db, source_id)
    actor = await _load_actor(db, principal)
    await feed_service.delete_source(db, source, actor)


@router.post(
    "/{source_id}/sync",
    response_model=FeedSyncAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_sync(
    source_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
    dry_run: bool = False,
) -> FeedSyncAccepted:
    source = await _active_source(db, source_id)
    actor = await _load_actor(db, principal)
    run = await feed_service.enqueue_sync(
        db,
        source,
        trigger=ChangeTrigger.feed_dry_run if dry_run else ChangeTrigger.feed_manual,
        dry_run=dry_run,
        actor=actor,
    )
    job = await _job_for_run(db, run.id)
    return FeedSyncAccepted(run=_run_response(run), job=_job_response(job))


@router.get("/{source_id}/syncs", response_model=list[FeedSyncRunResponse])
async def list_syncs(
    source_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(get_admin_principal)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FeedSyncRunResponse]:
    await _active_source(db, source_id)
    runs = list(
        (
            await db.scalars(
                select(FeedSyncRun)
                .where(FeedSyncRun.feed_source_id == source_id)
                .order_by(FeedSyncRun.sequence.desc(), FeedSyncRun.id.desc())
            )
        ).all()
    )
    return [_run_response(run) for run in runs]


async def _load_actor(db: AsyncSession, principal: Principal) -> User | None:
    return await db.get(User, principal.user_id)


async def _active_source(db: AsyncSession, source_id: uuid.UUID) -> ThreatFeedSource:
    source = await db.get(ThreatFeedSource, source_id)
    if source is None or source.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed source not found")
    return source


async def _job_for_run(db: AsyncSession, run_id: uuid.UUID) -> AgentJob:
    job = (
        await db.execute(select(AgentJob).where(AgentJob.feed_sync_run_id == run_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Feed sync job is missing",
        )
    return job


def _source_response(source: ThreatFeedSource) -> FeedSourceResponse:
    record = feed_service.source_record(source)
    return FeedSourceResponse(
        id=record.id,
        name=record.name,
        url=record.url,
        format=record.format,
        enabled=record.enabled,
        sync_interval_seconds=record.sync_interval_seconds,
        has_credential=record.has_credential,
        last_status=record.last_status,
        last_error=record.last_error,
        last_sync_at=record.last_sync_at,
        next_sync_at=record.next_sync_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_response(run: FeedSyncRun) -> FeedSyncRunResponse:
    return FeedSyncRunResponse(
        id=run.id,
        feed_source_id=run.feed_source_id,
        source_name=run.source_name,
        sequence=run.sequence,
        trigger=run.trigger,
        dry_run=run.dry_run,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        error=run.error,
        fetched_lines=run.fetched_lines,
        valid=run.valid,
        duplicates=run.duplicates,
        added=run.added,
        removed=run.removed,
        skipped_invalid=run.skipped_invalid,
        overlap_count=run.overlap_count,
        global_changed=run.global_changed,
        desired_revision=run.desired_revision,
        node_map_version=run.node_map_version,
    )


def _job_response(job: AgentJob) -> FeedSyncJobResponse:
    if job.feed_sync_run_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Feed sync job is missing its run",
        )
    return FeedSyncJobResponse(
        id=job.id,
        feed_sync_run_id=job.feed_sync_run_id,
        status=job.status,
        attempts=job.attempts,
        dispatched_at=job.dispatched_at,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
