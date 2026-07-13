from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import feeds
from app.core.config import get_settings
from app.core.deps import get_session_store
from app.core.security import hash_password
from app.core.sessions import RedisSessionStore
from app.db.models import (
    AgentJob,
    AuditEvent,
    ChangeTrigger,
    FeedSyncRun,
    FeedSyncStatus,
    JobStatus,
    Role,
    Tenant,
    ThreatFeedSource,
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
    app.include_router(feeds.router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_store] = lambda: store

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        yield client


async def create_admin(db_session: AsyncSession, username: str = "feeds-api-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant_user(db_session: AsyncSession) -> User:
    tenant = Tenant(name="Feeds API Tenant")
    user = User(
        username="feeds-api-tenant-user",
        role=Role.tenant_user,
        tenant=tenant,
        password_hash=hash_password("tenant-pass"),
    )
    db_session.add_all([tenant, user])
    await db_session.flush()
    return user


async def authenticate(client: AsyncClient, store: RedisSessionStore, user: User) -> None:
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    client.cookies.set(get_settings().session_cookie_name, sid)


def feed_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Abuse Feed",
        "url": "https://feeds.example.test/deny.txt",
        "sync_interval_seconds": 3600,
    }
    payload.update(overrides)
    return payload


async def create_feed(client: AsyncClient, **overrides: object) -> dict[str, object]:
    response = await client.post("/feeds", json=feed_payload(**overrides))
    assert response.status_code == 201
    return response.json()


async def test_admin_creates_feed_with_safe_source_contract_and_audit(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post(
            "/feeds",
            json=feed_payload(credential_env_var="THREAT_FEED_TOKEN"),
        )

    body = response.json()
    audit = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "feed.create"))
    ).scalar_one()
    assert response.status_code == 201
    assert body["name"] == "Abuse Feed"
    assert body["format"] == "line_list"
    assert body["enabled"] is True
    assert body["has_credential"] is True
    assert body["next_sync_at"] is not None
    assert "credential_env_var" not in body
    assert audit.target_id == body["id"]
    assert "THREAT_FEED_TOKEN" not in str(audit.metadata_)


async def test_admin_lists_and_gets_active_feeds_in_creation_order(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        first = await create_feed(client, name="First Feed")
        second = await create_feed(client, name="Second Feed")
        listed = await client.get("/feeds")
        fetched = await client.get(f"/feeds/{second['id']}")

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [first["id"], second["id"]]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == second["id"]


async def test_admin_updates_feed_and_hides_credential_reference(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        response = await client.put(
            f"/feeds/{created['id']}",
            json={
                "url": "https://feeds.example.test/replaced.txt",
                "sync_interval_seconds": 7200,
                "credential_env_var": "UPDATED_FEED_TOKEN",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["url"] == "https://feeds.example.test/replaced.txt"
    assert body["sync_interval_seconds"] == 7200
    assert body["has_credential"] is True
    assert "credential_env_var" not in body
    assert "UPDATED_FEED_TOKEN" not in response.text


async def test_disabling_feed_clears_schedule_without_deleting_source(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        response = await client.put(f"/feeds/{created['id']}", json={"enabled": False})
        fetched = await client.get(f"/feeds/{created['id']}")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["next_sync_at"] is None
    assert fetched.status_code == 200


async def test_admin_deletes_feed_audits_removal_and_hides_tombstone(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        deleted = await client.delete(f"/feeds/{created['id']}")
        fetched = await client.get(f"/feeds/{created['id']}")

    audit = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "feed.delete"))
    ).scalar_one()
    assert deleted.status_code == 204
    assert fetched.status_code == 404
    assert audit.target_id == created["id"]
    assert audit.metadata_["dangerous"] is True


async def test_manual_sync_returns_accepted_run_and_queued_job_status(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        response = await client.post(f"/feeds/{created['id']}/sync")

    body = response.json()
    assert response.status_code == 202
    assert body["run"]["feed_source_id"] == created["id"]
    assert body["run"]["trigger"] == "feed_manual"
    assert body["run"]["dry_run"] is False
    assert body["run"]["status"] == "queued"
    assert body["job"]["status"] == "queued"
    assert body["job"]["feed_sync_run_id"] == body["run"]["id"]


async def test_dry_run_sync_returns_accepted_dry_run_status(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        response = await client.post(f"/feeds/{created['id']}/sync", params={"dry_run": "true"})

    body = response.json()
    assert response.status_code == 202
    assert body["run"]["trigger"] == "feed_dry_run"
    assert body["run"]["dry_run"] is True
    assert body["run"]["status"] == "queued"
    assert body["job"]["status"] == "queued"


async def test_sync_history_is_sequence_descending_and_includes_run_counts(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        source = await db_session.get(ThreatFeedSource, created["id"])
        assert source is not None
        first = FeedSyncRun(
            feed_source_id=source.id,
            source_name=source.name,
            sequence=1,
            trigger=ChangeTrigger.feed_manual,
            status=FeedSyncStatus.success,
            fetched_lines=11,
            valid=10,
            duplicates=1,
            added=8,
            removed=2,
            skipped_invalid=3,
            overlap_count=4,
            global_changed=True,
            desired_revision=5,
            node_map_version=7,
        )
        second = FeedSyncRun(
            feed_source_id=source.id,
            source_name=source.name,
            sequence=2,
            trigger=ChangeTrigger.feed_dry_run,
            dry_run=True,
            status=FeedSyncStatus.partial,
            fetched_lines=20,
            valid=12,
            duplicates=2,
            added=0,
            removed=0,
            skipped_invalid=6,
            overlap_count=1,
            global_changed=False,
        )
        db_session.add_all([first, second])
        await db_session.flush()
        response = await client.get(f"/feeds/{created['id']}/syncs")

    body = response.json()
    assert response.status_code == 200
    assert [run["sequence"] for run in body] == [2, 1]
    assert body[0]["status"] == "partial"
    assert body[0]["dry_run"] is True
    assert body[0]["fetched_lines"] == 20
    assert body[0]["valid"] == 12
    assert body[0]["duplicates"] == 2
    assert body[0]["skipped_invalid"] == 6
    assert body[0]["overlap_count"] == 1
    assert body[1]["added"] == 8
    assert body[1]["removed"] == 2
    assert body[1]["desired_revision"] == 5
    assert body[1]["node_map_version"] == 7


async def test_safe_api_and_log_output_omit_credential_reference_and_value(
    caplog: pytest.LogCaptureFixture,
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    credential_reference = "PRIVATE_FEED_TOKEN"
    credential_value = "private-token-value"

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client, credential_env_var=credential_reference)
        listed = await client.get("/feeds")
        fetched = await client.get(f"/feeds/{created['id']}")
        updated = await client.put(
            f"/feeds/{created['id']}",
            json={"credential_env_var": credential_reference},
        )

    output = "\n".join((listed.text, fetched.text, updated.text, caplog.text))
    assert all(item["has_credential"] is True for item in listed.json())
    assert credential_reference not in output
    assert credential_value not in output
    assert "credential_env_var" not in output


@pytest.mark.parametrize(
    "invalid_payload",
    [
        feed_payload(url="http://feeds.example.test/deny.txt"),
        feed_payload(name="   "),
        feed_payload(sync_interval_seconds=299),
        feed_payload(sync_interval_seconds=604801),
        feed_payload(credential_env_var="feed_token"),
    ],
)
async def test_invalid_feed_configuration_returns_422(
    db_session: AsyncSession,
    redis_client: Redis,
    invalid_payload: dict[str, object],
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        response = await client.post("/feeds", json=invalid_payload)

    assert response.status_code == 422


async def test_duplicate_feed_name_returns_409(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        await create_feed(client, name="Duplicate Feed")
        response = await client.post("/feeds", json=feed_payload(name="duplicate feed"))

    assert response.status_code == 409
    assert response.json()["detail"] == "Feed source name already exists"


async def test_missing_or_deleted_feed_returns_404_for_resource_operations(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        await client.delete(f"/feeds/{created['id']}")
        responses = [
            await client.put(f"/feeds/{created['id']}", json={"enabled": True}),
            await client.post(f"/feeds/{created['id']}/sync"),
            await client.get(f"/feeds/{created['id']}/syncs"),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]


async def test_non_admin_feed_endpoints_are_403_without_mutation_or_partial_data(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant_user = await create_tenant_user(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        source_count_before = await db_session.scalar(
            select(func.count()).select_from(ThreatFeedSource)
        )
        run_count_before = await db_session.scalar(select(func.count()).select_from(FeedSyncRun))
        await authenticate(client, store, tenant_user)
        responses = [
            await client.post("/feeds", json=feed_payload(name="Denied Feed")),
            await client.get("/feeds"),
            await client.get(f"/feeds/{created['id']}"),
            await client.put(f"/feeds/{created['id']}", json={"enabled": False}),
            await client.delete(f"/feeds/{created['id']}"),
            await client.post(f"/feeds/{created['id']}/sync"),
            await client.get(f"/feeds/{created['id']}/syncs"),
        ]

    source_count_after = await db_session.scalar(select(func.count()).select_from(ThreatFeedSource))
    run_count_after = await db_session.scalar(select(func.count()).select_from(FeedSyncRun))
    assert [response.status_code for response in responses] == [403] * len(responses)
    assert all(response.json() == {"detail": "Forbidden"} for response in responses)
    assert (source_count_after, run_count_after) == (source_count_before, run_count_before)
    source = await db_session.get(ThreatFeedSource, created["id"])
    assert source is not None
    assert source.enabled is True


async def test_sync_accepted_response_matches_persisted_job(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)

    async for client in make_client(db_session, store):
        await authenticate(client, store, admin)
        created = await create_feed(client)
        response = await client.post(f"/feeds/{created['id']}/sync")

    body = response.json()
    job = await db_session.get(AgentJob, body["job"]["id"])
    assert response.status_code == 202
    assert job is not None
    assert job.status == JobStatus.queued
    assert str(job.feed_sync_run_id) == body["run"]["id"]
