from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.models import AlertScope, AlertSeverity, AlertState, ChannelKind, NotificationState


class AlertNotificationResponse(BaseModel):
    id: UUID
    channel_name: str
    kind: ChannelKind
    trigger: str
    state: NotificationState
    attempts: int
    last_error: str | None
    sent_at: datetime | None


class AlertResponse(BaseModel):
    id: UUID
    rule_key: str
    scope: AlertScope
    scope_key: str
    service_id: UUID | None
    tenant_id: UUID | None
    service_name: str | None
    severity: AlertSeverity
    state: AlertState
    metric_value: Decimal | None
    context: dict[str, Any]
    first_observed_at: datetime
    fired_at: datetime | None
    resolved_at: datetime | None
    acknowledged_at: datetime | None
    notifications: list[AlertNotificationResponse]


class AlertListResponse(BaseModel):
    alerts: list[AlertResponse]
    has_data: bool
