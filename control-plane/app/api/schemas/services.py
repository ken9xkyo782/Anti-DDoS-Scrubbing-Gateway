import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.db.models import ApplyStatus, ServiceMode


class ServicePlanCreateRequest(BaseModel):
    committed_clean_gbps: Decimal
    ceiling_clean_gbps: Decimal


class ServiceCreateRequest(BaseModel):
    tenant_id: uuid.UUID | None = None
    name: str
    cidr_or_ip: str
    mode: ServiceMode = ServiceMode.allow_rule_only
    vip_pps: int | None = None
    vip_bps: int | None = None
    service_pps: int | None = Field(default=None, ge=0)
    service_bps: int | None = Field(default=None, ge=0)
    plan: ServicePlanCreateRequest | None = None


class ServicePatchRequest(BaseModel):
    name: str | None = None
    cidr_or_ip: str | None = None
    mode: ServiceMode | None = None
    vip_pps: int | None = None
    vip_bps: int | None = None
    service_pps: int | None = Field(default=None, ge=0)
    service_bps: int | None = Field(default=None, ge=0)


class ServicePlanPatchRequest(BaseModel):
    committed_clean_gbps: Decimal
    ceiling_clean_gbps: Decimal


class ServiceDisableRequest(BaseModel):
    confirm: bool = False


class ServicePlanResponse(BaseModel):
    committed_clean_gbps: Decimal
    ceiling_clean_gbps: Decimal
    billing_metric: str
    overage_policy: str


class ServiceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None
    created_by: uuid.UUID | None
    creator_username: str | None
    name: str
    cidr_or_ip: str
    mode: ServiceMode
    enabled: bool
    vip_pps: int | None
    vip_bps: int | None
    service_pps: int | None
    service_bps: int | None
    apply_status: ApplyStatus
    version: int
    active_version: int | None
    plan: ServicePlanResponse
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class NextHopStatusResponse(BaseModel):
    dp_id: int
    dst_mac: str | None = None
    src_mac: str | None = None
    resolved: bool = False
    age_s: int | None = None
    success_count: int = 0
    failure_count: int = 0
