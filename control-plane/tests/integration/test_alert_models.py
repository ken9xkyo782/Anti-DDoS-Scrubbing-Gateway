import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Alert,
    AlertNotification,
    AlertRule,
    AlertScope,
    AlertSeverity,
    AlertState,
    ChannelKind,
    NotificationChannel,
    NotificationState,
    ProtectedService,
    Tenant,
)
from app.db.session import dispose_engine, get_session_factory

pytestmark = pytest.mark.integration


async def create_service(db_session: AsyncSession) -> ProtectedService:
    tenant = Tenant(name="Alert Model Tenant")
    service = ProtectedService(
        tenant=tenant,
        name="alert-edge",
        cidr_or_ip="203.0.113.10/32",
    )
    db_session.add_all([tenant, service])
    await db_session.flush()
    return service


def alert(**overrides: object) -> Alert:
    values: dict[str, object] = {
        "rule_key": "map_error",
        "scope": AlertScope.node,
        "scope_key": "node",
        "severity": AlertSeverity.critical,
        "state": AlertState.pending,
        "context": {"title": "Data-plane map error"},
        "first_observed_at": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return Alert(**values)


async def test_alert_partial_unique_deduplicates_non_resolved_rows_and_allows_resolved_history(
    db_session: AsyncSession,
) -> None:
    active = alert()
    db_session.add(active)
    await db_session.flush()

    async with db_session.begin_nested():
        db_session.add(alert())
        with pytest.raises(IntegrityError) as exc_info:
            await db_session.flush()

    assert "uq_alert_active_scope" in str(exc_info.value)

    active.state = AlertState.resolved
    active.resolved_at = datetime(2026, 7, 14, 12, 5, tzinfo=UTC)
    await db_session.flush()

    db_session.add(alert())
    await db_session.flush()


async def test_alert_history_survives_deleted_service_tenant_and_channel(
    db_session: AsyncSession,
) -> None:
    service = await create_service(db_session)
    channel = NotificationChannel(
        name="Tenant webhook",
        kind=ChannelKind.webhook,
        tenant_id=service.tenant_id,
        config={"url": "https://alerts.example.test/webhook"},
    )
    instance = alert(
        scope=AlertScope.service,
        scope_key=str(service.id),
        service_id=service.id,
        tenant_id=service.tenant_id,
        service_name=service.name,
    )
    notification = AlertNotification(
        alert=instance,
        channel=channel,
        channel_name=channel.name,
        kind=channel.kind,
        trigger="fire",
    )
    db_session.add_all([channel, instance, notification])
    await db_session.flush()

    await db_session.delete(service)
    await db_session.flush()
    await db_session.refresh(instance)

    assert instance.service_id is None
    assert instance.service_name == "alert-edge"

    await db_session.delete(channel)
    await db_session.flush()
    await db_session.refresh(notification)

    assert notification.channel_id is None
    assert notification.channel_name == "Tenant webhook"

    tenant = await db_session.get(Tenant, instance.tenant_id)
    assert tenant is not None
    await db_session.delete(tenant)
    await db_session.flush()
    await db_session.refresh(instance)

    assert instance.tenant_id is None


async def test_deleting_alert_cascades_its_notifications(db_session: AsyncSession) -> None:
    instance = alert()
    notification = AlertNotification(
        alert=instance,
        channel_name="Admin email",
        kind=ChannelKind.email,
        trigger="fire",
    )
    db_session.add_all([instance, notification])
    await db_session.flush()

    await db_session.delete(instance)
    await db_session.flush()

    assert await db_session.scalar(select(func.count(AlertNotification.id))) == 0


async def test_alert_models_round_trip_enums_and_optional_channel_secret(
    db_session: AsyncSession,
) -> None:
    rule = AlertRule(
        key="near_capacity",
        severity_override=AlertSeverity.warning,
        fire_threshold_override=Decimal("0.9000"),
        clear_threshold_override=Decimal("0.8500"),
    )
    channel = NotificationChannel(
        name="Admin email",
        kind=ChannelKind.email,
        min_severity=AlertSeverity.warning,
        config={"host": "smtp.example.test"},
    )
    instance = alert(
        rule_key=rule.key,
        severity=AlertSeverity.warning,
        state=AlertState.firing,
        metric_value=Decimal("0.9500"),
        fired_at=datetime(2026, 7, 14, 12, 1, tzinfo=UTC),
    )
    notification = AlertNotification(
        alert=instance,
        channel=channel,
        channel_name=channel.name,
        kind=channel.kind,
        trigger="fire",
        state=NotificationState.sent,
        sent_at=datetime(2026, 7, 14, 12, 2, tzinfo=UTC),
    )
    db_session.add_all([rule, channel, instance, notification])
    await db_session.flush()
    await db_session.refresh(rule)
    await db_session.refresh(channel)
    await db_session.refresh(instance)
    await db_session.refresh(notification)

    assert rule.severity_override is AlertSeverity.warning
    assert channel.kind is ChannelKind.email
    assert channel.min_severity is AlertSeverity.warning
    assert channel.secret is None
    assert instance.scope is AlertScope.node
    assert instance.severity is AlertSeverity.warning
    assert instance.state is AlertState.firing
    assert notification.kind is ChannelKind.email
    assert notification.state is NotificationState.sent
    assert [scope.value for scope in AlertScope] == ["node", "service"]
    assert [state.value for state in AlertState] == ["pending", "firing", "resolved"]
    assert [severity.value for severity in AlertSeverity] == ["info", "warning", "critical"]
    assert [kind.value for kind in ChannelKind] == ["email", "webhook"]
    assert [state.value for state in NotificationState] == ["pending", "sent", "retrying", "failed"]
    assert AlertRule.__table__.c.severity_override.type.native_enum is False
    assert NotificationChannel.__table__.c.kind.type.native_enum is False
    assert NotificationChannel.__table__.c.min_severity.type.native_enum is False
    assert Alert.__table__.c.scope.type.native_enum is False
    assert Alert.__table__.c.severity.type.native_enum is False
    assert Alert.__table__.c.state.type.native_enum is False
    assert AlertNotification.__table__.c.kind.type.native_enum is False
    assert AlertNotification.__table__.c.state.type.native_enum is False


async def test_alert_migration_upgrades_and_downgrades_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    del committed_db
    config = Config("alembic.ini")
    await dispose_engine()
    await asyncio.to_thread(downgrade, config, "20260714_0010")
    try:
        session_factory = get_session_factory()
        async with session_factory() as db_session:
            tables_before_upgrade = (
                (
                    await db_session.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' "
                            "AND tablename IN "
                            "('alert_rule', 'notification_channel', 'alert', 'alert_notification')"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert tables_before_upgrade == []

        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")

        session_factory = get_session_factory()
        async with session_factory() as db_session:
            tables_after_upgrade = (
                (
                    await db_session.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' "
                            "AND tablename IN "
                            "('alert_rule', 'notification_channel', 'alert', 'alert_notification') "
                            "ORDER BY tablename"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert tables_after_upgrade == [
            "alert",
            "alert_notification",
            "alert_rule",
            "notification_channel",
        ]
    finally:
        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")
