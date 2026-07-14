from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.db.models import BillingStatus, OveragePolicy


class BillingUsageResponse(BaseModel):
    service_id: UUID | None
    service_name: str
    tenant_id: UUID | None
    period_start: datetime
    period_end: datetime
    billing_metric: str
    committed_clean_gbps: Decimal
    p95_clean_gbps: Decimal
    billed_gbps: Decimal
    overage_gbps: Decimal
    overage_policy: OveragePolicy
    sample_count: int
    status: BillingStatus
    provisional: bool


class BillingUsageListResponse(BaseModel):
    usage: list[BillingUsageResponse]
    has_data: bool
