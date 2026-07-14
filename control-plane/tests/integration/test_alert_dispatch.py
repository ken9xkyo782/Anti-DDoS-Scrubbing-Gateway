import json
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Alert,
    AlertNotification,
    AlertScope,
    AlertSeverity,
    AlertState,
    ChannelKind,
    NotificationChannel,
    NotificationState,
    Tenant,
)
from app.worker.alert_dispatch import NotificationDispatcher

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def clear_alert_delivery_rows(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        await db.execute(delete(AlertNotification))
        await db.execute(delete(Alert))
        await db.execute(delete(NotificationChannel))
        await db.commit()
    yield
    async with committed_db() as db:
        await db.execute(delete(AlertNotification))
        await db.execute(delete(Alert))
        await db.execute(delete(NotificationChannel))
        await db.commit()


def alert(**overrides: object) -> Alert:
    values: dict[str, object] = {
        "rule_key": "map_error",
        "scope": AlertScope.node,
        "scope_key": "node",
        "severity": AlertSeverity.critical,
        "state": AlertState.firing,
        "metric_value": 1,
        "context": {"title": "Map error", "details": "safe context"},
        "first_observed_at": datetime(2026, 7, 14, 12, tzinfo=UTC),
        "fired_at": datetime(2026, 7, 14, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return Alert(**values)


async def add_channel(
    db: AsyncSession,
    *,
    name: str,
    tenant_id: UUID | None = None,
    kind: ChannelKind = ChannelKind.webhook,
    min_severity: AlertSeverity = AlertSeverity.info,
    url: str | None = None,
    secret: str | None = None,
) -> NotificationChannel:
    config: dict[str, object]
    if kind is ChannelKind.webhook:
        config = {"url": url or f"https://alerts.test/{name}"}
    else:
        config = {
            "smtp_host": "smtp.test",
            "port": 25,
            "from": "alerts@test.invalid",
            "to": ["ops@test.invalid"],
        }
    channel = NotificationChannel(
        name=name,
        kind=kind,
        tenant_id=tenant_id,
        min_severity=min_severity,
        config=config,
        secret=secret,
    )
    db.add(channel)
    await db.flush()
    return channel


async def test_routes_service_alerts_to_owner_and_admin_only_and_node_to_admin(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        dispatcher = NotificationDispatcher(client=client, max_attempts=2, retry_backoff_seconds=0)
        async with committed_db() as db:
            tenant_a = Tenant(name="Dispatch Tenant A")
            tenant_b = Tenant(name="Dispatch Tenant B")
            db.add_all([tenant_a, tenant_b])
            await db.flush()
            admin = await add_channel(db, name="admin")
            owner = await add_channel(db, name="owner", tenant_id=tenant_a.id)
            other = await add_channel(db, name="other", tenant_id=tenant_b.id)
            service_alert = alert(
                scope=AlertScope.service,
                scope_key="service-a",
                tenant_id=tenant_a.id,
            )
            node_alert = alert(scope_key="node-2")
            db.add_all([service_alert, node_alert])
            await db.flush()

            assert {
                channel.id for channel in await dispatcher.select_channels(db, service_alert)
            } == {
                admin.id,
                owner.id,
            }
            assert {channel.id for channel in await dispatcher.select_channels(db, node_alert)} == {
                admin.id
            }
            assert other.id not in {
                channel.id for channel in await dispatcher.select_channels(db, service_alert)
            }


async def test_min_severity_skips_channel_and_webhook_envelope_excludes_secret(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    received: list[dict[str, object]] = []

    async def webhook(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        dispatcher = NotificationDispatcher(client=client, max_attempts=2, retry_backoff_seconds=0)
        async with committed_db() as db:
            sent = await add_channel(db, name="info", secret="webhook-secret")
            await add_channel(db, name="critical-only", min_severity=AlertSeverity.critical)
            instance = alert(
                severity=AlertSeverity.warning,
                context={"title": "Capacity", "secret": "do-not-leak", "reason": "x" * 3_000},
            )
            db.add(instance)
            await db.flush()
            await dispatcher.enqueue(db, instance, "fire")
            await dispatcher.dispatch_pending(db)
            await db.commit()

    assert len(received) == 1
    assert received[0]["alert_id"]
    assert received[0]["rule"] == "map_error"
    assert received[0]["context"] == {"title": "Capacity", "reason": "x" * 2_000}
    assert "webhook-secret" not in str(received[0])
    async with committed_db() as db:
        notifications = list((await db.scalars(select(AlertNotification))).all())
    assert [(item.channel_id, item.state) for item in notifications] == [
        (sent.id, NotificationState.sent)
    ]


async def test_webhook_failure_retries_to_failed_without_blocking_other_channel(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    attempts: list[str] = []

    async def webhook(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        return httpx.Response(500 if request.url.path == "/broken" else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        dispatcher = NotificationDispatcher(client=client, max_attempts=2, retry_backoff_seconds=0)
        async with committed_db() as db:
            broken = await add_channel(db, name="broken", url="https://alerts.test/broken")
            healthy = await add_channel(db, name="healthy", url="https://alerts.test/healthy")
            instance = alert()
            db.add(instance)
            await db.flush()
            await dispatcher.enqueue(db, instance, "fire")
            await dispatcher.dispatch_pending(db)
            await db.commit()

            retry = await db.scalar(
                select(AlertNotification).where(AlertNotification.channel_id == broken.id)
            )
            assert retry is not None
            retry.updated_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
            await dispatcher.dispatch_pending(db)
            await db.commit()

    assert attempts.count("/broken") == 2
    assert attempts.count("/healthy") == 1
    async with committed_db() as db:
        notifications = {
            item.channel_id: item for item in (await db.scalars(select(AlertNotification))).all()
        }
    assert notifications[broken.id].state is NotificationState.failed
    assert notifications[broken.id].attempts == 2
    assert notifications[healthy.id].state is NotificationState.sent


async def test_email_delivery_uses_secret_without_exposing_it_in_message(
    committed_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            assert username == "alerts"
            assert password == "smtp-secret"

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr("app.worker.alert_dispatch.smtplib.SMTP", FakeSMTP)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        dispatcher = NotificationDispatcher(client=client, max_attempts=2, retry_backoff_seconds=0)
        async with committed_db() as db:
            channel = await add_channel(
                db,
                name="email",
                kind=ChannelKind.email,
                secret="smtp-secret",
            )
            channel.config["username"] = "alerts"
            instance = alert(context={"title": "Map error", "secret": "do-not-leak"})
            db.add(instance)
            await db.flush()
            await dispatcher.enqueue(db, instance, "fire")
            await dispatcher.dispatch_pending(db)
            await db.commit()

    assert len(sent) == 1
    assert "smtp-secret" not in sent[0].as_string()
    assert "do-not-leak" not in sent[0].as_string()


async def test_send_test_delivers_synthetic_alert_without_persisting_alert(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    received: list[dict[str, object]] = []

    async def webhook(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        dispatcher = NotificationDispatcher(client=client, max_attempts=2, retry_backoff_seconds=0)
        async with committed_db() as db:
            channel = await add_channel(db, name="test")
            result = await dispatcher.send_test(channel)
            await db.commit()

    assert result.state is NotificationState.sent
    assert received[0]["rule"] == "test"
    async with committed_db() as db:
        assert await db.scalar(select(Alert.id)) is None
