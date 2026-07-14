import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.alerts import AlertListResponse, AlertNotificationResponse, AlertResponse
from app.core.deps import Principal, get_current_user, load_service_for_principal
from app.db.models import Alert, AlertScope, AlertSeverity, AlertState, Role
from app.db.session import get_db

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


@router.get("/{alert_id}", response_model=AlertResponse)
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
