import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import TenantStatus


class TenantCreateRequest(BaseModel):
    name: str


class TenantPatchRequest(BaseModel):
    name: str | None = None
    status: TenantStatus | None = None


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    active_allocation_count: int
    user_count: int
