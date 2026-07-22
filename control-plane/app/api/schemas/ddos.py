import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BlockedPortCreateRequest(BaseModel):
    port: int = Field(ge=0, le=65535)
    note: str | None = Field(default=None, max_length=256)


class BlockedPortResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    port: int
    note: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime


class AmplificationConfigResponse(BaseModel):
    hardcoded_ports: list[int]
    dynamic_ports: list[BlockedPortResponse]
