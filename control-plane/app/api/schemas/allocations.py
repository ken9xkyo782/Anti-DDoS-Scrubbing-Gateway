import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import CIDRStatus


class AllocationCreateRequest(BaseModel):
    tenant_id: uuid.UUID
    cidr: str


class OverlapCheckRequest(BaseModel):
    cidr: str


class AllocationResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    cidr: str
    status: CIDRStatus
    allocated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AllocationUsageResponse(BaseModel):
    allocation: AllocationResponse
    dependent_count: int


class OverlapCheckResponse(BaseModel):
    overlaps: bool
    conflicts: list[AllocationResponse]
