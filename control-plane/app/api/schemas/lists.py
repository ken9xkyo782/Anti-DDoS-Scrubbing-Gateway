import uuid
from datetime import datetime

from pydantic import BaseModel

from typing import Literal

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
    scope: Literal[BlacklistScope.global_] = BlacklistScope.global_
    source: BlacklistSource
    source_cidr: str
    created_by: uuid.UUID | None
    created_at: datetime
