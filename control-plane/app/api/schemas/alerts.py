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


class AlertRuleResponse(BaseModel):
    key: str
    enabled: bool
    severity: AlertSeverity
    fire_threshold: float
    clear_threshold: float
    silence_in_maintenance: bool


class AlertRuleListResponse(BaseModel):
    rules: list[AlertRuleResponse]


class AlertRulePatchRequest(BaseModel):
    enabled: bool | None = None
    severity: AlertSeverity | None = None
    fire_threshold: float | None = None
    clear_threshold: float | None = None
    silence_in_maintenance: bool | None = None


class NotificationChannelRequest(BaseModel):
    name: str | None = None
    kind: ChannelKind | None = None
    tenant_id: UUID | None = None
    enabled: bool | None = None
    min_severity: AlertSeverity | None = None
    config: dict[str, Any] | None = None
    secret: str | None = None


class NotificationChannelCreateRequest(NotificationChannelRequest):
    name: str
    kind: ChannelKind
    config: dict[str, Any]


class NotificationChannelResponse(BaseModel):
    id: UUID
    name: str
    kind: ChannelKind
    tenant_id: UUID | None
    enabled: bool
    min_severity: AlertSeverity
    config: dict[str, Any]


class NotificationChannelListResponse(BaseModel):
    channels: list[NotificationChannelResponse]


class AlertChannelTestResponse(BaseModel):
    state: NotificationState
    attempts: int
    error: str | None
