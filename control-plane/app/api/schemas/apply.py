import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import ApplyStatus, ChangeTrigger, JobStatus, JobType


class JobView(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    version: int
    job_type: JobType
    trigger: ChangeTrigger
    status: JobStatus
    error: str | None
    attempts: int
    dispatched_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ApplyStatusView(BaseModel):
    service_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None
    apply_status: ApplyStatus
    version: int
    active_version: int | None
    last_error: str | None
    last_applied_at: datetime | None
    latest_job: JobView | None


class ApplyMutationResponse(BaseModel):
    apply_status: ApplyStatus
    version: int
    active_version: int | None
