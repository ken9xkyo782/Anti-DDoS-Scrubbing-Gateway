import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.alerts import (
    AlertChannelTestResponse,
    AlertListResponse,
    AlertNotificationResponse,
    AlertResponse,
    AlertRuleListResponse,
    AlertRulePatchRequest,
    AlertRuleResponse,
    NotificationChannelCreateRequest,
    NotificationChannelListResponse,
    NotificationChannelRequest,
    NotificationChannelResponse,
)
from app.core.config import get_settings
from app.core.deps import Principal, get_current_user, load_service_for_principal, require_admin
from app.db.models import (
    Alert,
    AlertRule,
    AlertScope,
    AlertSeverity,
    AlertState,
    ChannelKind,
    NotificationChannel,
    Role,
    User,
)
from app.db.session import get_db
from app.services.alert_rules import RULES, RuleDef
from app.services.audit import record_event
from app.worker.alert_dispatch import NotificationDispatcher

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    state: AlertState | None = None,
    severity: AlertSeverity | None = None,
    scope: AlertScope | None = None,
    service_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> AlertListResponse:
    if service_id is not None:
        await load_service_for_principal(db, service_id, principal)
    statement = select(Alert).options(selectinload(Alert.notifications))
    if principal.role is not Role.admin:
        if principal.tenant_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        statement = statement.where(
            Alert.scope == AlertScope.service,
            Alert.tenant_id == principal.tenant_id,
        )
    if state is not None:
        statement = statement.where(Alert.state == state)
    if severity is not None:
        statement = statement.where(Alert.severity == severity)
    if scope is not None:
        statement = statement.where(Alert.scope == scope)
    if service_id is not None:
        statement = statement.where(Alert.service_id == service_id)
    if since is not None:
        statement = statement.where(Alert.first_observed_at >= since)
    records = list(
        (await db.scalars(statement.order_by(Alert.first_observed_at.desc(), Alert.id.desc())))
        .unique()
        .all()
    )
    responses = [_response(alert) for alert in records]
    return AlertListResponse(alerts=responses, has_data=bool(responses))


@router.get("/{alert_id:uuid}", response_model=AlertResponse)
async def get_alert(
    alert_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertResponse:
    statement = select(Alert).options(selectinload(Alert.notifications)).where(Alert.id == alert_id)
    if principal.role is not Role.admin:
        if principal.tenant_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        statement = statement.where(
            Alert.scope == AlertScope.service,
            Alert.tenant_id == principal.tenant_id,
        )
    alert = (await db.scalars(statement)).unique().one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return _response(alert)


def _response(alert: Alert) -> AlertResponse:
    return AlertResponse(
        id=alert.id,
        rule_key=alert.rule_key,
        scope=alert.scope,
        scope_key=alert.scope_key,
        service_id=alert.service_id,
        tenant_id=alert.tenant_id,
        service_name=alert.service_name,
        severity=alert.severity,
        state=alert.state,
        metric_value=alert.metric_value,
        context=alert.context,
        first_observed_at=alert.first_observed_at,
        fired_at=alert.fired_at,
        resolved_at=alert.resolved_at,
        acknowledged_at=alert.acknowledged_at,
        notifications=[
            AlertNotificationResponse(
                id=notification.id,
                channel_name=notification.channel_name,
                kind=notification.kind,
                trigger=notification.trigger,
                state=notification.state,
                attempts=notification.attempts,
                last_error=notification.last_error,
                sent_at=notification.sent_at,
            )
            for notification in alert.notifications
        ],
    )


async def get_notification_dispatcher() -> AsyncGenerator[NotificationDispatcher, None]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.worker_alert_delivery_timeout_seconds) as client:
        yield NotificationDispatcher(
            client=client,
            max_attempts=settings.worker_alert_max_attempts,
            smtp_timeout_seconds=settings.worker_alert_delivery_timeout_seconds,
        )


@router.get("/rules", response_model=AlertRuleListResponse)
async def list_rules(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertRuleListResponse:
    require_admin(principal)
    overrides = {row.key: row for row in (await db.scalars(select(AlertRule))).all()}
    return AlertRuleListResponse(
        rules=[_rule_response(definition, overrides.get(definition.key)) for definition in RULES]
    )


@router.patch("/rules/{key}", response_model=AlertRuleResponse)
async def patch_rule(
    key: str,
    payload: AlertRulePatchRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertRuleResponse:
    require_admin(principal)
    definition = next((rule for rule in RULES if rule.key == key), None)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    rule = await db.scalar(select(AlertRule).where(AlertRule.key == key))
    if rule is None:
        rule = AlertRule(key=key, silence_in_maintenance=definition.silence_in_maintenance)
        db.add(rule)
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    if payload.severity is not None:
        rule.severity_override = payload.severity
    if payload.fire_threshold is not None:
        rule.fire_threshold_override = Decimal(str(payload.fire_threshold))
    if payload.clear_threshold is not None:
        rule.clear_threshold_override = Decimal(str(payload.clear_threshold))
    if payload.silence_in_maintenance is not None:
        rule.silence_in_maintenance = payload.silence_in_maintenance
    await db.flush()
    await _audit(db, principal, "alert.rule.update", "alert_rule", str(rule.id), {"key": key})
    return _rule_response(definition, rule)


@router.get("/channels", response_model=NotificationChannelListResponse)
async def list_channels(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationChannelListResponse:
    require_admin(principal)
    channels = list(
        (await db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))).all()
    )
    return NotificationChannelListResponse(
        channels=[_channel_response(channel) for channel in channels]
    )


@router.post(
    "/channels", response_model=NotificationChannelResponse, status_code=status.HTTP_201_CREATED
)
async def create_channel(
    payload: NotificationChannelCreateRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationChannelResponse:
    require_admin(principal)
    _validate_channel_config(payload.kind, payload.config)
    channel = NotificationChannel(
        name=payload.name,
        kind=payload.kind,
        tenant_id=payload.tenant_id,
        enabled=True if payload.enabled is None else payload.enabled,
        min_severity=payload.min_severity or AlertSeverity.info,
        config=payload.config,
        secret=payload.secret,
    )
    db.add(channel)
    await db.flush()
    await _audit(
        db,
        principal,
        "alert.channel.create",
        "notification_channel",
        str(channel.id),
        {"name": channel.name, "kind": channel.kind.value, "config_keys": sorted(channel.config)},
    )
    return _channel_response(channel)


@router.patch("/channels/{channel_id}", response_model=NotificationChannelResponse)
async def patch_channel(
    channel_id: uuid.UUID,
    payload: NotificationChannelRequest,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotificationChannelResponse:
    require_admin(principal)
    channel = await _load_channel(db, channel_id)
    kind = payload.kind or channel.kind
    config = payload.config if payload.config is not None else channel.config
    _validate_channel_config(kind, config)
    if payload.name is not None:
        channel.name = payload.name
    channel.kind = kind
    if payload.tenant_id is not None:
        channel.tenant_id = payload.tenant_id
    if payload.enabled is not None:
        channel.enabled = payload.enabled
    if payload.min_severity is not None:
        channel.min_severity = payload.min_severity
    channel.config = config
    if payload.secret is not None:
        channel.secret = payload.secret
    await db.flush()
    await _audit(db, principal, "alert.channel.update", "notification_channel", str(channel.id), {})
    return _channel_response(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    require_admin(principal)
    channel = await _load_channel(db, channel_id)
    await _audit(db, principal, "alert.channel.delete", "notification_channel", str(channel.id), {})
    await db.delete(channel)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/channels/{channel_id}/test", response_model=AlertChannelTestResponse)
async def test_channel(
    channel_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    dispatcher: Annotated[NotificationDispatcher, Depends(get_notification_dispatcher)],
) -> AlertChannelTestResponse:
    require_admin(principal)
    channel = await _load_channel(db, channel_id)
    result = await dispatcher.send_test(channel)
    return AlertChannelTestResponse(
        state=result.state, attempts=result.attempts, error=result.error
    )


def _rule_response(definition: RuleDef, row: AlertRule | None) -> AlertRuleResponse:
    rule = definition
    return AlertRuleResponse(
        key=rule.key,
        enabled=rule.default_enabled if row is None else row.enabled,
        severity=AlertSeverity(rule.severity.value)
        if row is None or row.severity_override is None
        else row.severity_override,
        fire_threshold=float(
            rule.fire_threshold
            if row is None or row.fire_threshold_override is None
            else row.fire_threshold_override
        ),
        clear_threshold=float(
            rule.clear_threshold
            if row is None or row.clear_threshold_override is None
            else row.clear_threshold_override
        ),
        silence_in_maintenance=rule.silence_in_maintenance
        if row is None
        else row.silence_in_maintenance,
    )


def _channel_response(channel: NotificationChannel) -> NotificationChannelResponse:
    return NotificationChannelResponse(
        id=channel.id,
        name=channel.name,
        kind=channel.kind,
        tenant_id=channel.tenant_id,
        enabled=channel.enabled,
        min_severity=channel.min_severity,
        config=channel.config,
    )


async def _load_channel(db: AsyncSession, channel_id: uuid.UUID) -> NotificationChannel:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification channel not found"
        )
    return channel


async def _audit(
    db: AsyncSession,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, object],
) -> None:
    await record_event(
        db,
        actor=await db.get(User, principal.user_id),
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome="success",
        metadata=metadata,
    )


def _validate_channel_config(kind: ChannelKind, config: dict[str, object]) -> None:
    if kind is ChannelKind.webhook:
        if not isinstance(config.get("url"), str) or not config["url"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Webhook URL required"
            )
        return
    recipients = config.get("to")
    if (
        not isinstance(config.get("smtp_host"), str)
        or not isinstance(config.get("from"), str)
        or not isinstance(recipients, list)
        or not recipients
        or not all(isinstance(value, str) and value for value in recipients)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="SMTP configuration required"
        )
