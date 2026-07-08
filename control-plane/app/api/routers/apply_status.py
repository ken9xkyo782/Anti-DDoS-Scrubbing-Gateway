import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.apply import ApplyMutationResponse, ApplyStatusView, JobView
from app.core.deps import Principal, get_current_user, load_service_for_principal, require_admin
from app.db.models import AgentJob, JobStatus, User
from app.db.session import get_db
from app.services import apply as apply_service

router = APIRouter(tags=["apply-status"])


@router.get("/services/{service_id}/apply-status", response_model=ApplyStatusView)
async def get_apply_status(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyStatusView:
    service = await load_service_for_principal(db, service_id, principal)
    return _apply_status_view(await apply_service.get_apply_status(db, service))


@router.post(
    "/services/{service_id}/apply-status/retry",
    response_model=ApplyMutationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_apply_status(
    service_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApplyMutationResponse:
    service = await load_service_for_principal(db, service_id, principal)
    actor = await db.get(User, principal.user_id)
    try:
        await apply_service.retry(db, service, actor)
    except apply_service.NotFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Apply status is not failed",
        ) from exc
    return ApplyMutationResponse(
        apply_status=service.apply_status,
        version=service.version,
        active_version=service.active_version,
    )


@router.get("/jobs", response_model=list[JobView])
async def list_jobs(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> list[JobView]:
    require_admin(principal)
    return [_job_view(job) for job in await apply_service.list_jobs(db, status=status_filter)]


def _apply_status_view(record: apply_service.ApplyStatusRecord) -> ApplyStatusView:
    return ApplyStatusView(
        service_id=record.service_id,
        tenant_id=record.tenant_id,
        tenant_name=record.tenant_name,
        apply_status=record.apply_status,
        version=record.version,
        active_version=record.active_version,
        last_error=record.last_error,
        last_applied_at=record.last_applied_at,
        latest_job=_job_view(record.latest_job) if record.latest_job is not None else None,
    )


def _job_view(job: AgentJob) -> JobView:
    return JobView(
        id=job.id,
        target_type=job.target_type,
        target_id=job.target_id,
        version=job.version,
        job_type=job.job_type,
        trigger=job.trigger,
        status=job.status,
        error=job.error,
        attempts=job.attempts,
        dispatched_at=job.dispatched_at,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
