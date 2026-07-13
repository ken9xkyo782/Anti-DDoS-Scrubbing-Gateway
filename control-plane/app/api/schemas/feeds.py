import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import ChangeTrigger, FeedFormat, FeedSyncStatus, JobStatus


class FeedSourceCreateRequest(BaseModel):
    name: str
    url: str
    sync_interval_seconds: int
    format: FeedFormat = FeedFormat.line_list
    enabled: bool = True
    credential_env_var: str | None = None


class FeedSourceUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    sync_interval_seconds: int | None = None
    format: FeedFormat | None = None
    enabled: bool | None = None
    credential_env_var: str | None = None


class FeedSourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    format: FeedFormat
    enabled: bool
    sync_interval_seconds: int
    has_credential: bool
    last_status: FeedSyncStatus | None
    last_error: str | None
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FeedSyncRunResponse(BaseModel):
    id: uuid.UUID
    feed_source_id: uuid.UUID
    source_name: str
    sequence: int
    trigger: ChangeTrigger
    dry_run: bool
    status: FeedSyncStatus
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    fetched_lines: int
    valid: int
    duplicates: int
    added: int
    removed: int
    skipped_invalid: int
    overlap_count: int
    global_changed: bool
    desired_revision: int | None
    node_map_version: int | None


class FeedSyncJobResponse(BaseModel):
    id: uuid.UUID
    feed_sync_run_id: uuid.UUID
    status: JobStatus
    attempts: int
    dispatched_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class FeedSyncAccepted(BaseModel):
    run: FeedSyncRunResponse
    job: FeedSyncJobResponse
