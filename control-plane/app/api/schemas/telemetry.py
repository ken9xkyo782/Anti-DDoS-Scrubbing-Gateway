from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.db.models import FeedSyncStatus, XdpMode


class TelemetryWindowResponse(BaseModel):
    has_data: bool
    clean_pkts: int
    clean_bytes: int
    drop_pkts: int
    drop_bytes: int
    drop_by_reason: dict[str, int]
    pps: int
    bps: int
    window_start: datetime | None
    window_seconds: int
    stale: bool


class FeedSourceStatusResponse(BaseModel):
    id: UUID
    name: str
    enabled: bool
    last_status: FeedSyncStatus | None
    last_sync_at: datetime | None


class JobBacklogResponse(BaseModel):
    queued: int
    applying: int


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
    job_backlog: JobBacklogResponse
    feed_sources: list[FeedSourceStatusResponse]
