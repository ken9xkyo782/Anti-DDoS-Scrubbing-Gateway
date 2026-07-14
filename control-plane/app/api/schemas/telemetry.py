from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.db.models import FeedSyncStatus, JobStatus, JobType, XdpMode


class TelemetryWindowResponse(BaseModel):
    has_data: bool
    clean_pkts: int
    clean_bytes: int
    drop_pkts: int
    drop_bytes: int
    drop_by_reason: dict[str, int]
    pps: int
    bps: int
    top_dst_ports: list[dict[str, int]]
    top_src: list[dict[str, int | str]]
    committed_clean_bps: int
    committed_honored: bool | None
    window_start: datetime | None
    window_seconds: int
    stale: bool


class TelemetryWindowPoint(BaseModel):
    window_start: datetime
    window_seconds: int
    clean_pkts: int
    clean_bytes: int
    drop_pkts: int
    drop_bytes: int
    pps: int
    bps: int


class TelemetryHistoryResponse(BaseModel):
    has_data: bool
    windows: list[TelemetryWindowPoint]


class FeedSyncRunStatusResponse(BaseModel):
    id: UUID
    sequence: int
    status: FeedSyncStatus
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    valid: int
    added: int
    removed: int
    skipped_invalid: int
    overlap_count: int


class FeedSourceStatusResponse(BaseModel):
    id: UUID
    name: str
    enabled: bool
    last_status: FeedSyncStatus | None
    last_error: str | None
    last_sync_at: datetime | None
    last_run: FeedSyncRunStatusResponse | None


class JobBacklogResponse(BaseModel):
    queued: int
    applying: int


class LastApplyResponse(BaseModel):
    id: UUID
    job_type: JobType
    status: JobStatus
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CommittedServiceResponse(BaseModel):
    service_id: UUID
    observed_clean_bps: int
    committed_clean_bps: int
    honored: bool | None
    window_start: datetime | None


class NodeHealthResponse(BaseModel):
    has_data: bool
    xdp_mode: XdpMode
    active_slot: int | None
    map_version: int | None
    map_error_count: int
    node_clean_bps: int
    node_capacity_bps: int
    window_start: datetime | None
    window_seconds: int
    stale: bool
    bloom_stats: dict[str, int]
    committed_services: list[CommittedServiceResponse]
    job_backlog: JobBacklogResponse
    last_apply: LastApplyResponse | None
    feed_sources: list[FeedSourceStatusResponse]
