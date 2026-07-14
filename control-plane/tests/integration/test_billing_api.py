from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import billing
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import (
    BillingStatus,
    BillingUsage,
    OveragePolicy,
    ProtectedService,
    Role,
    Tenant,
    User,
)
from app.db.session import get_db

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(billing.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def create_user(
    db_session: AsyncSession,
    *,
    username: str,
    role: Role,
    tenant: Tenant | None = None,
) -> User:
    user = User(
        username=username,
        role=role,
        tenant=tenant,
        password_hash=hash_password("billing-api-pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    name: str,
    cidr: str,
) -> ProtectedService:
    service = ProtectedService(tenant=tenant, name=name, cidr_or_ip=cidr)
    db_session.add(service)
    await db_session.flush()
    return service


def usage(
    *,
    service: ProtectedService,
    status: BillingStatus,
    period_start: datetime = datetime(2026, 7, 1, tzinfo=UTC),
) -> BillingUsage:
    return BillingUsage(
        service_id=service.id,
        tenant_id=service.tenant_id,
        service_name=service.name,
        period_start=period_start,
        period_end=datetime(2026, 8, 1, tzinfo=UTC),
        billing_metric="p95_clean_bps",
        committed_clean_gbps=Decimal("1.00"),
        p95_clean_gbps=Decimal("1.25"),
        billed_gbps=Decimal("1.25"),
        overage_gbps=Decimal("0.25"),
        overage_policy=OveragePolicy.billed,
        sample_count=12,
        status=status,
        finalized_at=datetime(2026, 8, 1, tzinfo=UTC) if status is BillingStatus.final else None,
    )


async def test_tenant_usage_is_scoped_and_open_rows_are_explicitly_provisional(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    owner_tenant = Tenant(name="Billing API Owner")
    other_tenant = Tenant(name="Billing API Other")
    db_session.add_all([owner_tenant, other_tenant])
    await db_session.flush()
    owner = await create_user(
        db_session,
        username="billing-api-owner",
        role=Role.tenant_user,
        tenant=owner_tenant,
    )
    owned_service = await create_service(
        db_session,
        tenant=owner_tenant,
        name="owned-edge",
        cidr="203.0.113.180/32",
    )
    other_service = await create_service(
        db_session,
        tenant=other_tenant,
        name="other-edge",
        cidr="203.0.113.181/32",
    )
    db_session.add_all(
        [
            usage(service=owned_service, status=BillingStatus.open),
            usage(service=other_service, status=BillingStatus.final),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, owner)
        response = await client.get("/billing/usage", params={"period": "2026-07"})

    assert response.status_code == 200
    assert response.json() == {
        "has_data": True,
        "usage": [
            {
                "service_id": str(owned_service.id),
                "service_name": "owned-edge",
                "tenant_id": str(owner_tenant.id),
                "period_start": "2026-07-01T00:00:00Z",
                "period_end": "2026-08-01T00:00:00Z",
                "billing_metric": "p95_clean_bps",
                "committed_clean_gbps": "1.00",
                "p95_clean_gbps": "1.25",
                "billed_gbps": "1.25",
                "overage_gbps": "0.25",
                "overage_policy": "billed",
                "sample_count": 12,
                "status": "open",
                "provisional": True,
            }
        ],
    }


async def test_tenant_service_filter_returns_404_for_a_cross_tenant_service(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    owner_tenant = Tenant(name="Billing API Filter Owner")
    other_tenant = Tenant(name="Billing API Filter Other")
    db_session.add_all([owner_tenant, other_tenant])
    await db_session.flush()
    owner = await create_user(
        db_session,
        username="billing-api-filter-owner",
        role=Role.tenant_user,
        tenant=owner_tenant,
    )
    other_service = await create_service(
        db_session,
        tenant=other_tenant,
        name="filtered-other-edge",
        cidr="203.0.113.182/32",
    )

    async for client in make_client(db_session, store):
        await authenticate(client, store, owner)
        response = await client.get("/billing/usage", params={"service_id": other_service.id})

    assert response.status_code == 404
    assert response.json() == {"detail": "Service not found"}


async def test_admin_usage_lists_all_tenants_and_honors_filters(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    first_tenant = Tenant(name="Billing API Admin First")
    second_tenant = Tenant(name="Billing API Admin Second")
    db_session.add_all([first_tenant, second_tenant])
    await db_session.flush()
    admin = await create_user(
        db_session,
        username="billing-api-admin",
        role=Role.admin,
    )
    first_service = await create_service(
        db_session,
        tenant=first_tenant,
        name="admin-first-edge",
        cidr="203.0.113.183/32",
    )
    second_service = await create_service(
        db_session,
        tenant=second_tenant,
        name="admin-second-edge",
        cidr="203.0.113.184/32",
    )
    db_session.add_all(
        [
            usage(service=first_service, status=BillingStatus.final),
            usage(service=second_service, status=BillingStatus.open),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        all_usage = await client.get("/billing/usage")
        filtered = await client.get(
            "/billing/usage",
            params={"tenant_id": first_tenant.id, "status": "final"},
        )

    assert all_usage.status_code == 200
    assert {entry["service_id"] for entry in all_usage.json()["usage"]} == {
        str(first_service.id),
        str(second_service.id),
    }
    assert filtered.status_code == 200
    assert [entry["service_id"] for entry in filtered.json()["usage"]] == [str(first_service.id)]


async def test_usage_empty_state_and_invalid_period_return_expected_responses(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Billing API Empty")
    db_session.add(tenant)
    await db_session.flush()
    tenant_user = await create_user(
        db_session,
        username="billing-api-empty-user",
        role=Role.tenant_user,
        tenant=tenant,
    )

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        empty = await client.get("/billing/usage")
        invalid_period = await client.get("/billing/usage", params={"period": "2026-13"})

    assert empty.status_code == 200
    assert empty.json() == {"usage": [], "has_data": False}
    assert invalid_period.status_code == 422


async def test_export_csv_returns_only_finalized_rows(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Billing API Export CSV")
    db_session.add(tenant)
    await db_session.flush()
    admin = await create_user(
        db_session,
        username="billing-api-export-csv-admin",
        role=Role.admin,
    )
    final_service = await create_service(
        db_session,
        tenant=tenant,
        name="final-csv-edge",
        cidr="203.0.113.185/32",
    )
    open_service = await create_service(
        db_session,
        tenant=tenant,
        name="open-csv-edge",
        cidr="203.0.113.186/32",
    )
    db_session.add_all(
        [
            usage(service=final_service, status=BillingStatus.final),
            usage(service=open_service, status=BillingStatus.open),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.get(
            "/billing/usage/export",
            params={"period": "2026-07", "format": "csv"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.splitlines() == [
        "service,tenant,period,committed,p95,billed,overage,overage_policy,sample_count",
        "final-csv-edge,Billing API Export CSV,2026-07,1.00,1.25,1.25,0.25,billed,12",
    ]


async def test_export_json_returns_only_finalized_rows_and_requires_admin(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Billing API Export JSON")
    db_session.add(tenant)
    await db_session.flush()
    admin = await create_user(
        db_session,
        username="billing-api-export-json-admin",
        role=Role.admin,
    )
    tenant_user = await create_user(
        db_session,
        username="billing-api-export-json-tenant",
        role=Role.tenant_user,
        tenant=tenant,
    )
    final_service = await create_service(
        db_session,
        tenant=tenant,
        name="final-json-edge",
        cidr="203.0.113.187/32",
    )
    open_service = await create_service(
        db_session,
        tenant=tenant,
        name="open-json-edge",
        cidr="203.0.113.188/32",
    )
    db_session.add_all(
        [
            usage(service=final_service, status=BillingStatus.final),
            usage(service=open_service, status=BillingStatus.open),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        denied = await client.get(
            "/billing/usage/export",
            params={"period": "2026-07", "format": "json"},
        )
        await authenticate(client, store, admin)
        exported = await client.get(
            "/billing/usage/export",
            params={"period": "2026-07", "format": "json"},
        )

    assert denied.status_code == 403
    assert exported.status_code == 200
    assert exported.json()["has_data"] is True
    assert [entry["service_id"] for entry in exported.json()["usage"]] == [str(final_service.id)]
    assert exported.json()["usage"][0]["status"] == "final"
    assert exported.json()["usage"][0]["provisional"] is False


async def test_history_returns_finalized_periods_newest_first_with_a_limit(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    tenant = Tenant(name="Billing API History")
    db_session.add(tenant)
    await db_session.flush()
    tenant_user = await create_user(
        db_session,
        username="billing-api-history-user",
        role=Role.tenant_user,
        tenant=tenant,
    )
    service = await create_service(
        db_session,
        tenant=tenant,
        name="history-edge",
        cidr="203.0.113.189/32",
    )
    db_session.add_all(
        [
            usage(
                service=service,
                status=BillingStatus.final,
                period_start=datetime(2026, 5, 1, tzinfo=UTC),
            ),
            usage(
                service=service,
                status=BillingStatus.final,
                period_start=datetime(2026, 7, 1, tzinfo=UTC),
            ),
            usage(
                service=service,
                status=BillingStatus.open,
                period_start=datetime(2026, 8, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, tenant_user)
        response = await client.get(
            "/billing/usage/history",
            params={"service_id": service.id, "limit": 2},
        )

    assert response.status_code == 200
    assert response.json()["has_data"] is True
    assert [(entry["status"], entry["period_start"]) for entry in response.json()["usage"]] == [
        ("final", "2026-07-01T00:00:00Z"),
        ("final", "2026-05-01T00:00:00Z"),
    ]


async def test_history_is_tenant_scoped_and_hides_cross_tenant_services(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    owner_tenant = Tenant(name="Billing API History Owner")
    other_tenant = Tenant(name="Billing API History Other")
    db_session.add_all([owner_tenant, other_tenant])
    await db_session.flush()
    owner = await create_user(
        db_session,
        username="billing-api-history-owner",
        role=Role.tenant_user,
        tenant=owner_tenant,
    )
    owner_service = await create_service(
        db_session,
        tenant=owner_tenant,
        name="history-owner-edge",
        cidr="203.0.113.190/32",
    )
    other_service = await create_service(
        db_session,
        tenant=other_tenant,
        name="history-other-edge",
        cidr="203.0.113.191/32",
    )
    db_session.add_all(
        [
            usage(service=owner_service, status=BillingStatus.final),
            usage(service=other_service, status=BillingStatus.final),
        ]
    )
    await db_session.flush()

    async for client in make_client(db_session, store):
        await authenticate(client, store, owner)
        scoped = await client.get("/billing/usage/history")
        denied = await client.get(
            "/billing/usage/history",
            params={"service_id": other_service.id},
        )

    assert scoped.status_code == 200
    assert scoped.json()["has_data"] is True
    assert [entry["service_id"] for entry in scoped.json()["usage"]] == [str(owner_service.id)]
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Service not found"}
