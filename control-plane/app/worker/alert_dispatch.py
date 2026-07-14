"""Scoped, durable delivery for alert notification channels."""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Alert,
    AlertNotification,
    AlertScope,
    AlertSeverity,
    AlertState,
    ChannelKind,
    NotificationChannel,
    NotificationState,
)
from app.services.audit import scrub_metadata

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {
    AlertSeverity.info: 0,
    AlertSeverity.warning: 1,
    AlertSeverity.critical: 2,
}
_CONTEXT_STRING_LIMIT = 2_000


@dataclass(frozen=True, slots=True)
class TestDeliveryResult:
    state: NotificationState
    attempts: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _SyntheticAlert:
    id: uuid.UUID
    rule_key: str
    severity: AlertSeverity
    scope: AlertScope
    state: AlertState
    service_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    fired_at: datetime | None
    resolved_at: datetime | None
    metric_value: Decimal | None
    context: dict[str, Any]


class NotificationDispatcher:
    """Persist routed notifications and deliver each channel independently."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 5.0,
        smtp_timeout_seconds: float = 10.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.client = client
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.smtp_timeout_seconds = smtp_timeout_seconds

    async def select_channels(
        self,
        db: AsyncSession,
        alert: Alert,
    ) -> list[NotificationChannel]:
        """Return only channels structurally eligible for this alert's scope."""
        channels = list(
            (
                await db.scalars(
                    select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
                )
            ).all()
        )
        return [channel for channel in channels if self._matches(channel, alert)]

    async def enqueue(self, db: AsyncSession, alert: Alert, trigger: str) -> None:
        """Create one durable notification per routed channel."""
        channels = await self.select_channels(db, alert)
        for channel in channels:
            db.add(
                AlertNotification(
                    alert=alert,
                    channel=channel,
                    channel_name=channel.name,
                    kind=channel.kind,
                    trigger=trigger,
                )
            )
        await db.flush()

    async def dispatch_pending(self, db: AsyncSession) -> None:
        """Attempt pending or due retrying notifications without cross-channel failure."""
        notifications = list(
            (
                await db.scalars(
                    select(AlertNotification)
                    .options(
                        selectinload(AlertNotification.alert),
                        selectinload(AlertNotification.channel),
                    )
                    .where(
                        AlertNotification.state.in_(
                            (NotificationState.pending, NotificationState.retrying)
                        )
                    )
                    .order_by(AlertNotification.created_at)
                )
            ).all()
        )
        now = datetime.now(UTC)
        for notification in notifications:
            if not self._retry_due(notification, now):
                continue
            if notification.alert is None or notification.channel is None:
                self._mark_failure(notification)
                continue
            try:
                await self.deliver(db, notification, notification.alert, notification.channel)
            except Exception:
                logger.exception("Alert notification delivery failed")
                self._mark_failure(notification)
        await db.flush()

    async def deliver(
        self,
        db: AsyncSession,
        notification: AlertNotification,
        alert: Alert,
        channel: NotificationChannel,
    ) -> None:
        """Deliver once, recording a bounded retry state on ordinary transport failure."""
        del db
        try:
            await self._send(channel, alert)
        except Exception:
            self._mark_failure(notification)
            return
        notification.attempts += 1
        notification.state = NotificationState.sent
        notification.last_error = None
        notification.sent_at = datetime.now(UTC)

    async def send_test(self, channel: NotificationChannel) -> TestDeliveryResult:
        """Deliver a synthetic test without creating an Alert or AlertNotification row."""
        synthetic = _SyntheticAlert(
            id=uuid.uuid4(),
            rule_key="test",
            severity=AlertSeverity.info,
            scope=AlertScope.node,
            state=AlertState.firing,
            service_id=None,
            tenant_id=None,
            fired_at=datetime.now(UTC),
            resolved_at=None,
            metric_value=None,
            context={"title": "Alert channel test"},
        )
        try:
            await self._send(channel, synthetic)
        except Exception:
            return TestDeliveryResult(
                state=NotificationState.failed,
                attempts=1,
                error="Alert channel test delivery failed",
            )
        return TestDeliveryResult(state=NotificationState.sent, attempts=1)

    def _matches(self, channel: NotificationChannel, alert: Alert) -> bool:
        if _SEVERITY_ORDER[channel.min_severity] > _SEVERITY_ORDER[alert.severity]:
            return False
        if alert.scope is AlertScope.node:
            return channel.tenant_id is None
        return channel.tenant_id is None or channel.tenant_id == alert.tenant_id

    def _retry_due(self, notification: AlertNotification, now: datetime) -> bool:
        if notification.state is NotificationState.pending:
            return True
        if notification.attempts >= self.max_attempts:
            notification.state = NotificationState.failed
            return False
        backoff = self.retry_backoff_seconds * (2 ** max(notification.attempts - 1, 0))
        return now - notification.updated_at >= timedelta(seconds=backoff)

    def _mark_failure(self, notification: AlertNotification) -> None:
        notification.attempts += 1
        notification.last_error = "Alert channel delivery failed"
        notification.state = (
            NotificationState.failed
            if notification.attempts >= self.max_attempts
            else NotificationState.retrying
        )

    async def _send(self, channel: NotificationChannel, alert: Alert | _SyntheticAlert) -> None:
        envelope = _envelope(alert)
        if channel.kind is ChannelKind.webhook:
            await self._send_webhook(channel, envelope)
            return
        if channel.kind is ChannelKind.email:
            await asyncio.to_thread(self._send_email, channel, envelope)
            return
        raise ValueError("Unsupported alert channel kind")

    async def _send_webhook(
        self,
        channel: NotificationChannel,
        envelope: dict[str, Any],
    ) -> None:
        url = channel.config.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("Webhook URL is not configured")
        headers = _headers(channel.config.get("headers"))
        if channel.secret is not None:
            headers.setdefault("Authorization", f"Bearer {channel.secret}")
        response = await self.client.post(url, json=envelope, headers=headers)
        response.raise_for_status()

    def _send_email(self, channel: NotificationChannel, envelope: dict[str, Any]) -> None:
        host = channel.config.get("smtp_host")
        recipients = channel.config.get("to")
        sender = channel.config.get("from")
        if not isinstance(host, str) or not host:
            raise ValueError("SMTP host is not configured")
        if not isinstance(sender, str) or not sender:
            raise ValueError("SMTP sender is not configured")
        if not isinstance(recipients, Sequence) or isinstance(recipients, str):
            raise ValueError("SMTP recipients are not configured")
        recipient_list = [value for value in recipients if isinstance(value, str) and value]
        if not recipient_list:
            raise ValueError("SMTP recipients are not configured")

        port = channel.config.get("port", 25)
        if not isinstance(port, int):
            raise ValueError("SMTP port is invalid")
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipient_list)
        message["Subject"] = str(envelope["title"])
        message.set_content(json.dumps(envelope, default=str, sort_keys=True))

        with smtplib.SMTP(host, port, timeout=self.smtp_timeout_seconds) as smtp:
            if channel.config.get("use_tls") is True:
                smtp.starttls()
            username = channel.config.get("username")
            if isinstance(username, str) and username and channel.secret is not None:
                smtp.login(username, channel.secret)
            smtp.send_message(message)


def _headers(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): str(header_value)
        for key, header_value in value.items()
        if isinstance(key, str) and isinstance(header_value, (str, int, float))
    }


def _envelope(alert: Alert | _SyntheticAlert) -> dict[str, Any]:
    context = _bounded_context(alert.context)
    envelope: dict[str, Any] = {
        "alert_id": str(alert.id),
        "rule": alert.rule_key,
        "severity": alert.severity.value,
        "scope": alert.scope.value,
        "state": alert.state.value,
        "fired_at": _timestamp(alert.fired_at),
        "metric": str(alert.metric_value) if alert.metric_value is not None else None,
        "title": context.get("title", alert.rule_key),
        "context": context,
    }
    if alert.service_id is not None:
        envelope["service_id"] = str(alert.service_id)
    if alert.tenant_id is not None:
        envelope["tenant_id"] = str(alert.tenant_id)
    if alert.resolved_at is not None:
        envelope["resolved_at"] = _timestamp(alert.resolved_at)
    return envelope


def _bounded_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    scrubbed = scrub_metadata(context)
    return {key: _truncate(value) for key, value in scrubbed.items()}


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_CONTEXT_STRING_LIMIT]
    if isinstance(value, Mapping):
        return {str(key): _truncate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate(item) for item in value]
    return value


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
