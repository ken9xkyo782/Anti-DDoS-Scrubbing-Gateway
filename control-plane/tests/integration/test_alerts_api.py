from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import alerts
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import (
    Alert,
    AlertNotification,
    AlertScope,
    AlertSeverity,
    AlertState,
    AuditEvent,
    ChannelKind,
    NotificationState,
    ProtectedService,
    Role,
    Tenant,
    User,
)
from app.db.session import get_db

pytestmark = pytest.mark.integration


def store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def client_for(
    db_session: AsyncSession, session_store: RedisSessionStore
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(alerts.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: session_store
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ) as client:
        yield client


async def login(client: AsyncClient, session_store: RedisSessionStore, user: User) -> None:
    sid = await session_store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def user(db: AsyncSession, username: str, role: Role, tenant: Tenant | None = None) -> User:
    value = User(
        username=username,
        role=role,
        tenant=tenant,
        password_hash=hash_password("alerts-api-password"),
    )
    db.add(value)
    await db.flush()
    return value


async def test_alert_history_is_admin_wide_and_tenant_scoped_with_notification_detail(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    tenant_a = Tenant(name="Alerts API A")
    tenant_b = Tenant(name="Alerts API B")
    service_a = ProtectedService(tenant=tenant_a, name="alerts-a", cidr_or_ip="203.0.113.90/32")
    service_b = ProtectedService(tenant=tenant_b, name="alerts-b", cidr_or_ip="203.0.113.91/32")
    db_session.add_all([tenant_a, tenant_b, service_a, service_b])
    await db_session.flush()
    admin = await user(db_session, "alerts-admin", Role.admin)
    tenant_user = await user(db_session, "alerts-tenant", Role.tenant_user, tenant_a)
    node = Alert(
        rule_key="map_error",
        scope=AlertScope.node,
        scope_key="node",
        severity=AlertSeverity.critical,
        state=AlertState.firing,
        context={"title": "Map error"},
        fire_streak=1,
        clear_streak=0,
        first_observed_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    own = Alert(
        rule_key="attack_onset",
        scope=AlertScope.service,
        scope_key=str(service_a.id),
        service_id=service_a.id,
        tenant_id=tenant_a.id,
        service_name=service_a.name,
        severity=AlertSeverity.warning,
        state=AlertState.firing,
        context={"title": "Attack"},
        fire_streak=1,
        clear_streak=0,
        first_observed_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    other = Alert(
        rule_key="attack_onset",
        scope=AlertScope.service,
        scope_key=str(service_b.id),
        service_id=service_b.id,
        tenant_id=tenant_b.id,
        service_name=service_b.name,
        severity=AlertSeverity.warning,
        state=AlertState.firing,
        context={"title": "Attack"},
        fire_streak=1,
        clear_streak=0,
        first_observed_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    notification = AlertNotification(
        alert=own,
        channel_name="Owner webhook",
        kind=ChannelKind.webhook,
        trigger="fire",
        state=NotificationState.sent,
        attempts=1,
    )
    db_session.add_all([node, own, other, notification])
    await db_session.flush()

    session_store = store(redis_client)
    async for client in client_for(db_session, session_store):
        await login(client, session_store, admin)
        admin_list = await client.get("/alerts")
        await login(client, session_store, tenant_user)
        tenant_list = await client.get("/alerts")
        own_detail = await client.get(f"/alerts/{own.id}")
        node_detail = await client.get(f"/alerts/{node.id}")
        other_detail = await client.get(f"/alerts/{other.id}")
        service_filter = await client.get(f"/alerts?service_id={service_b.id}")

    assert admin_list.status_code == 200
    assert len(admin_list.json()["alerts"]) == 3
    assert tenant_list.json()["has_data"] is True
    assert [item["id"] for item in tenant_list.json()["alerts"]] == [str(own.id)]
    assert own_detail.status_code == 200
    assert own_detail.json()["notifications"][0]["state"] == "sent"
    assert node_detail.status_code == 404
    assert other_detail.status_code == 404
    assert service_filter.status_code == 404


async def test_alert_history_returns_empty_state_and_filters_for_admin(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    admin = await user(db_session, "alerts-empty-admin", Role.admin)
    session_store = store(redis_client)
    async for client in client_for(db_session, session_store):
        await login(client, session_store, admin)
        response = await client.get("/alerts?state=resolved&severity=warning&scope=service")

    assert response.status_code == 200
    assert response.json() == {"alerts": [], "has_data": False}


async def test_alert_admin_rule_and_channel_configuration_is_audited_and_secrets_are_write_only(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    admin = await user(db_session, "alerts-config-admin", Role.admin)
    tenant = Tenant(name="Alerts config tenant")
    db_session.add(tenant)
    tenant_user = await user(db_session, "alerts-config-tenant", Role.tenant_user, tenant)
    session_store = store(redis_client)
    async for client in client_for(db_session, session_store):
        await login(client, session_store, admin)
        defaults = await client.get("/alerts/rules")
        patch = await client.patch(
            "/alerts/rules/map_error",
            json={"enabled": False, "fire_threshold": 2},
        )
        created = await client.post(
            "/alerts/channels",
            json={
                "name": "Admin webhook",
                "kind": "webhook",
                "config": {"url": "https://alerts.test/hook"},
                "secret": "never-return-this",
            },
        )
        channels = await client.get("/alerts/channels")
        await login(client, session_store, tenant_user)
        forbidden = await client.get("/alerts/channels")

    assert defaults.status_code == 200
    assert any(rule["key"] == "map_error" for rule in defaults.json()["rules"])
    assert patch.status_code == 200
    assert patch.json()["enabled"] is False
    assert created.status_code == 201
    assert "secret" not in created.json()
    assert channels.json()["channels"][0]["name"] == "Admin webhook"
    assert "never-return-this" not in str(channels.json())
    assert forbidden.status_code == 403
    assert await db_session.scalar(
        select(AuditEvent.id).where(AuditEvent.action == "alert.channel.create")
    )


async def test_alert_acknowledges_own_firing_alert_without_resolving_and_admin_exports(
    db_session: AsyncSession, redis_client: Redis
) -> None:
    tenant = Tenant(name="Alerts ack tenant")
    service = ProtectedService(tenant=tenant, name="alerts-ack", cidr_or_ip="203.0.113.92/32")
    db_session.add_all([tenant, service])
    await db_session.flush()
    admin = await user(db_session, "alerts-export-admin", Role.admin)
    tenant_user = await user(db_session, "alerts-ack-tenant", Role.tenant_user, tenant)
    instance = Alert(
        rule_key="attack_onset",
        scope=AlertScope.service,
        scope_key=str(service.id),
        service_id=service.id,
        tenant_id=tenant.id,
        service_name=service.name,
        severity=AlertSeverity.warning,
        state=AlertState.firing,
        context={"title": "Attack"},
        fire_streak=1,
        clear_streak=0,
        first_observed_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    db_session.add(instance)
    await db_session.flush()
    session_store = store(redis_client)
    async for client in client_for(db_session, session_store):
        await login(client, session_store, tenant_user)
        acknowledged = await client.post(f"/alerts/{instance.id}/ack")
        tenant_export = await client.get("/alerts/export?format=json")
        await login(client, session_store, admin)
        exported_json = await client.get("/alerts/export?format=json")
        exported_csv = await client.get("/alerts/export?format=csv")

    await db_session.refresh(instance)
    assert acknowledged.status_code == 200
    assert acknowledged.json()["state"] == "firing"
    assert instance.acknowledged_at is not None
    assert tenant_export.status_code == 403
    assert exported_json.status_code == 200
    assert exported_json.json()["alerts"][0]["id"] == str(instance.id)
    assert exported_csv.status_code == 200
    assert "attack_onset" in exported_csv.text
    assert await db_session.scalar(select(AuditEvent.id).where(AuditEvent.action == "alert.acknowledge"))
