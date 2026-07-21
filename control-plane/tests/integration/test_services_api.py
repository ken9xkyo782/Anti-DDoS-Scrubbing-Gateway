from collections.abc import AsyncGenerator
from ipaddress import IPv4Network

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import services
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import AllowRule, AuditEvent, ProtectedService, Protocol, Role, Tenant, User
from app.db.session import get_db
from app.services import allocations as allocation_service

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_client(
    db_session: AsyncSession,
    store: RedisSessionStore,
    nexthop_writer: object = None,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(services.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store
    if nexthop_writer is not None:
        app.dependency_overrides[services.get_nexthop_writer] = lambda: nexthop_writer

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "services-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(db_session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def create_tenant_user(
    db_session: AsyncSession,
    *,
    username: str,
    tenant: Tenant,
) -> User:
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash=hash_password("tenant-pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


async def allocate(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
    cidr: str,
) -> None:
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network(cidr),
        actor=actor,
    )


async def create_service_via_api(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    name: str = "edge",
    cidr_or_ip: str = "203.0.113.10/32",
) -> ProtectedService:
    response = await client.post(
        "/services",
        json={"tenant_id": str(tenant.id), "name": name, "cidr_or_ip": cidr_or_ip},
    )
    assert response.status_code == 202
    assert response.json() == {
        "apply_status": "queued",
        "version": 1,
        "active_version": None,
    }
    service = (
        await db_session.execute(
            select(ProtectedService).where(
                ProtectedService.tenant_id == tenant.id,
                ProtectedService.name == name,
            )
        )
    ).scalar_one()
    return service


async def test_create_service_inside_allocation_returns_202_queued(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Services API Create Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "edge",
                "cidr_or_ip": "203.0.113.10/32",
                "plan": {"committed_clean_gbps": "2", "ceiling_clean_gbps": "5"},
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "apply_status": "queued",
        "version": 1,
        "active_version": None,
    }


async def test_create_service_outside_allocation_returns_403(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-outside-admin")
    tenant = await create_tenant(db_session, "Services API Outside Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "outside",
                "cidr_or_ip": "198.51.100.10/32",
            },
        )

    assert response.status_code == 403


@pytest.mark.parametrize("cidr_or_ip", ["2001:db8::/48", "203.0.113.10/24"])
async def test_create_service_invalid_cidr_returns_422(
    db_session: AsyncSession,
    redis_client: Redis,
    cidr_or_ip: str,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, f"services-api-invalid-{cidr_or_ip}")
    tenant = await create_tenant(db_session, f"Services API Invalid {cidr_or_ip}")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/services",
            json={"tenant_id": str(tenant.id), "name": "invalid", "cidr_or_ip": cidr_or_ip},
        )

    assert response.status_code == 422


async def test_create_service_overlap_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-overlap-admin")
    tenant = await create_tenant(db_session, "Services API Overlap Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="wide",
            cidr_or_ip="203.0.113.10/32",
        )
        response = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "nested",
                "cidr_or_ip": "203.0.113.10/32",
            },
        )

    assert response.status_code == 409
    assert "wide" in response.text


async def test_create_service_plan_invariant_returns_422(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-plan-invalid-admin")
    tenant = await create_tenant(db_session, "Services API Plan Invalid Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "bad-plan",
                "cidr_or_ip": "203.0.113.10/32",
                "plan": {"committed_clean_gbps": "5", "ceiling_clean_gbps": "2"},
            },
        )

    assert response.status_code == 422


async def test_tenant_user_plan_patch_returns_403_and_admin_warning(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-plan-admin")
    tenant = await create_tenant(db_session, "Services API Plan Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="services-api-plan-user", tenant=tenant
    )
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        first = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="first",
            cidr_or_ip="203.0.113.10/32",
        )
        second = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="second",
            cidr_or_ip="203.0.113.20/32",
        )
        await client.patch(
            f"/services/{first.id}/plan",
            json={"committed_clean_gbps": "39", "ceiling_clean_gbps": "39"},
        )
        await client.post(f"/services/{first.id}/enable")
        await client.post(f"/services/{second.id}/enable")
        await authenticate(client, store, tenant_user)
        tenant_denied = await client.patch(
            f"/services/{second.id}/plan",
            json={"committed_clean_gbps": "1", "ceiling_clean_gbps": "1"},
        )
        await authenticate(client, store, admin)
        admin_warning = await client.patch(
            f"/services/{second.id}/plan",
            json={"committed_clean_gbps": "5", "ceiling_clean_gbps": "5"},
        )

    assert tenant_denied.status_code == 403
    assert admin_warning.status_code == 202
    assert admin_warning.json()["apply_status"] == "queued"


async def test_list_services_admin_owner_annotation_and_tenant_scope(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-list-admin")
    own_tenant = await create_tenant(db_session, "Services API Own Tenant")
    other_tenant = await create_tenant(db_session, "Services API Other Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="services-api-list-user", tenant=own_tenant
    )
    await allocate(db_session, tenant=own_tenant, actor=admin, cidr="203.0.113.0/24")
    await allocate(db_session, tenant=other_tenant, actor=admin, cidr="198.51.100.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        own = await create_service_via_api(
            client,
            db_session,
            tenant=own_tenant,
            name="own",
            cidr_or_ip="203.0.113.10/32",
        )
        other = await create_service_via_api(
            client,
            db_session,
            tenant=other_tenant,
            name="other",
            cidr_or_ip="198.51.100.10/32",
        )
        admin_list = await client.get("/services")
        await authenticate(client, store, tenant_user)
        tenant_list = await client.get("/services")

    assert {row["id"] for row in admin_list.json()} == {str(own.id), str(other.id)}
    assert admin_list.json()[0]["tenant_name"] is not None
    assert [row["id"] for row in tenant_list.json()] == [str(own.id)]


async def test_enable_disable_confirm_and_idempotent_audit(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-toggle-admin")
    tenant = await create_tenant(db_session, "Services API Toggle Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        service = await create_service_via_api(client, db_session, tenant=tenant)
        enabled = await client.post(f"/services/{service.id}/enable")
        missing_confirm = await client.post(f"/services/{service.id}/disable", json={})
        disabled = await client.post(f"/services/{service.id}/disable", json={"confirm": True})
        disabled_again = await client.post(
            f"/services/{service.id}/disable",
            json={"confirm": True},
        )

    audits = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "service.disable"))
    ).scalars()
    assert enabled.status_code == 202
    assert missing_confirm.status_code == 422
    assert disabled.status_code == 202
    assert disabled_again.status_code == 202
    assert len(list(audits)) == 1


async def test_delete_enabled_then_disable_delete_cascades(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-delete-admin")
    tenant = await create_tenant(db_session, "Services API Delete Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        service = await create_service_via_api(client, db_session, tenant=tenant)
        db_session.add(AllowRule(service_id=service.id, priority=10, protocol=Protocol.tcp))
        await db_session.flush()
        await client.post(f"/services/{service.id}/enable")
        enabled_delete = await client.delete(f"/services/{service.id}")
        await client.post(f"/services/{service.id}/disable", json={"confirm": True})
        disabled_delete = await client.delete(f"/services/{service.id}")

    assert enabled_delete.status_code == 409
    assert disabled_delete.status_code == 204
    assert (await db_session.execute(select(AllowRule))).scalars().all() == []


async def test_patch_destination_overlap_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-patch-overlap-admin")
    tenant = await create_tenant(db_session, "Services API Patch Overlap Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        first = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            cidr_or_ip="203.0.113.10/32",
        )
        second = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="second",
            cidr_or_ip="203.0.113.20/32",
        )
        response = await client.patch(
            f"/services/{second.id}",
            json={"cidr_or_ip": str(first.cidr_or_ip)},
        )

    assert response.status_code == 409


async def test_cross_tenant_service_access_is_zero_leak_404(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "services-api-cross-admin")
    own_tenant = await create_tenant(db_session, "Services API Cross Own Tenant")
    other_tenant = await create_tenant(db_session, "Services API Cross Other Tenant")
    other_user = await create_tenant_user(
        db_session, username="services-api-cross-user", tenant=other_tenant
    )
    await allocate(db_session, tenant=own_tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        service = await create_service_via_api(
            client,
            db_session,
            tenant=own_tenant,
            name="secret-edge",
            cidr_or_ip="203.0.113.10/32",
        )
        await authenticate(client, store, other_user)
        response = await client.get(f"/services/{service.id}")

    assert response.status_code == 404
    assert "secret-edge" not in response.text
    assert str(own_tenant.id) not in response.text


async def test_create_service_api_non_32_cidr_rejected(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "api-non-32-admin")
    tenant = await create_tenant(db_session, "API Non-32 Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "wide",
                "cidr_or_ip": "203.0.113.0/24",
            },
        )
    assert response.status_code == 422
    assert "Service destination must be a single host" in response.text


async def test_update_service_api_non_32_cidr_rejected(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "api-non-32-update-admin")
    tenant = await create_tenant(db_session, "API Non-32 Update Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        service = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="valid",
            cidr_or_ip="203.0.113.10/32",
        )
        response = await client.patch(
            f"/services/{service.id}",
            json={
                "cidr_or_ip": "203.0.113.0/24",
            },
        )
    assert response.status_code == 422
    assert "Service destination must be a single host" in response.text


async def test_admin_can_trigger_nexthop_resolve_and_read_status(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    from app.worker.nexthop_resolver import FakeNextHopWriter

    store = make_store(redis_client)
    admin = await create_admin(db_session, "nexthop-trigger-admin")
    tenant = await create_tenant(db_session, "Nexthop Trigger Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    fake_writer = FakeNextHopWriter(resolve_results=[True])

    async for client in make_client(db_session, store, nexthop_writer=fake_writer):
        await authenticate(client, store, admin)
        service = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="nh-trigger-svc",
            cidr_or_ip="203.0.113.50/32",
        )
        trigger_res = await client.post(f"/services/{service.id}/resolve-nexthop")
        status_res = await client.get(f"/services/{service.id}/nexthop")

    assert trigger_res.status_code == 200
    assert trigger_res.json()["dp_id"] > 0
    assert trigger_res.json()["success_count"] >= 1
    assert status_res.status_code == 200
    assert status_res.json()["dp_id"] == trigger_res.json()["dp_id"]
    assert status_res.json()["success_count"] >= 1


async def test_non_admin_cannot_trigger_nexthop_resolve(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "nexthop-rbac-admin")
    tenant = await create_tenant(db_session, "Nexthop RBAC Tenant")
    tenant_user = await create_tenant_user(
        db_session, username="nexthop-tenant-user", tenant=tenant
    )
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        service = await create_service_via_api(
            client,
            db_session,
            tenant=tenant,
            name="nh-rbac-svc",
            cidr_or_ip="203.0.113.51/32",
        )
        await authenticate(client, store, tenant_user)
        trigger_res = await client.post(f"/services/{service.id}/resolve-nexthop")

    assert trigger_res.status_code == 403


async def test_service_rate_limit_fields_api_round_trip(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, "svc-rl-admin")
    tenant = await create_tenant(db_session, "Svc RL Tenant")
    await allocate(db_session, tenant=tenant, actor=admin, cidr="203.0.113.0/24")

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        
        # Test creation with valid service rate limits
        create_res = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "rl-svc",
                "cidr_or_ip": "203.0.113.80/32",
                "service_pps": 5000,
                "service_bps": 100000,
            },
        )
        assert create_res.status_code == 202
        
        # Query service and check rates
        services_res = await client.get("/services")
        assert services_res.status_code == 200
        service_data = [s for s in services_res.json() if s["name"] == "rl-svc"][0]
        assert service_data["service_pps"] == 5000
        assert service_data["service_bps"] == 100000
        
        # Test PATCH updates
        patch_res = await client.patch(
            f"/services/{service_data['id']}",
            json={
                "service_pps": 2500,
                "service_bps": 50000,
            },
        )
        assert patch_res.status_code == 202
        
        # Verify PATCH updates
        get_res = await client.get(f"/services/{service_data['id']}")
        assert get_res.status_code == 200
        assert get_res.json()["service_pps"] == 2500
        assert get_res.json()["service_bps"] == 50000

        # Test schema validation rejects negative values
        neg_res = await client.post(
            "/services",
            json={
                "tenant_id": str(tenant.id),
                "name": "rl-svc-neg",
                "cidr_or_ip": "203.0.113.81/32",
                "service_pps": -5,
            },
        )
        assert neg_res.status_code == 422
