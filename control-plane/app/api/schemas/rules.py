import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import Protocol


class RuleCreateRequest(BaseModel):
    priority: int
    protocol: Protocol
    src_port_lo: int | None = None
    src_port_hi: int | None = None
    dst_port_lo: int | None = None
    dst_port_hi: int | None = None
    pps: int | None = None
    bps: int | None = None
    enabled: bool = True


class RulePatchRequest(BaseModel):
    priority: int | None = None
    protocol: Protocol | None = None
    src_port_lo: int | None = None
    src_port_hi: int | None = None
    dst_port_lo: int | None = None
    dst_port_hi: int | None = None
    pps: int | None = None
    bps: int | None = None
    enabled: bool | None = None


class RuleOverlapCheckRequest(BaseModel):
    protocol: Protocol
    src_port_lo: int | None = None
    src_port_hi: int | None = None
    dst_port_lo: int | None = None
    dst_port_hi: int | None = None


class RuleResponse(BaseModel):
    id: uuid.UUID
    service_id: uuid.UUID
    priority: int
    protocol: Protocol
    src_port_lo: int | None
    src_port_hi: int | None
    dst_port_lo: int | None
    dst_port_hi: int | None
    pps: int | None
    bps: int | None
    enabled: bool
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RuleOverlapCheckResponse(BaseModel):
    warnings: list[str]
