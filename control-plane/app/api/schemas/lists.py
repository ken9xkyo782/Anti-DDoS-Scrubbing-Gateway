import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import BlacklistScope, BlacklistSource


class ListEntryCreateRequest(BaseModel):
    source_cidr: str


class WhitelistEntryResponse(BaseModel):
    id: uuid.UUID
    service_id: uuid.UUID
    source_cidr: str
    created_by: uuid.UUID | None
    created_at: datetime


class BlacklistEntryResponse(BaseModel):
    id: uuid.UUID
    service_id: uuid.UUID | None
    scope: BlacklistScope
    source: BlacklistSource
    source_cidr: str
    created_by: uuid.UUID | None
    created_at: datetime
