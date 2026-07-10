import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.feed_parser import parse_line_list
from app.db.models import (
    AgentJob,
    AuditEvent,
    BlacklistEntry,
    BlacklistScope,
    ChangeTrigger,
    FeedFormat,
    FeedSyncRun,
    JobStatus,
    JobType,
    Role,
    ThreatFeedSource,
    User,
    utc_now,
)
from app.db.session import run_post_commit_callbacks
from app.services import feed_reconcile
from app.services import feeds as feed_service
from app.services.apply import APPLY_QUEUE_KEY

pytestmark = pytest.mark.integration


async def create_admin(db: AsyncSession, username: str = "feed-admin") -> User:
    actor = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db.add(actor)
    await db.flush()
    return actor


def payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "Example Feed",
        "url": "https://feeds.example.test/deny.txt",
        "sync_interval_seconds": 3600,
    }
    value.update(overrides)
    return value


async def source_job(db: AsyncSession, run: FeedSyncRun) -> AgentJob:
    return (
        await db.execute(select(AgentJob).where(AgentJob.feed_sync_run_id == run.id))
    ).scalar_one()


async def test_create_source_is_enabled_immediately_due_and_audited(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    before = utc_now()

    source = await feed_service.create_source(db_session, payload(), actor)

    assert source.enabled is True
    assert source.format == FeedFormat.line_list
    assert source.next_sync_at is not None
    assert source.next_sync_at >= before
    audit = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "feed.create"))
    ).scalar_one()
    assert audit.target_id == str(source.id)


@pytest.mark.parametrize(
    "invalid_payload",
    [
        payload(url="http://feeds.example.test/deny.txt"),
        payload(url="https://user:pass@feeds.example.test/deny.txt"),
        payload(url="https://feeds.example.test/deny.txt#fragment"),
        payload(sync_interval_seconds=299),
        payload(sync_interval_seconds=604801),
        payload(format="json"),
        payload(credential_env_var="feed_token"),
    ],
)
async def test_create_source_rejects_invalid_configuration(
    db_session: AsyncSession,
    invalid_payload: dict[str, object],
) -> None:
    actor = await create_admin(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await feed_service.create_source(db_session, invalid_payload, actor)

    assert exc_info.value.status_code == 422


async def test_create_source_rejects_case_insensitive_name_collision(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    await feed_service.create_source(db_session, payload(name="Abuse Feed"), actor)

    with pytest.raises(HTTPException) as exc_info:
        await feed_service.create_source(db_session, payload(name="abuse feed"), actor)

    assert exc_info.value.status_code == 409


async def test_update_url_or_credential_and_reenable_make_source_due_now(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    source = await feed_service.create_source(db_session, payload(), actor)
    source.next_sync_at = utc_now() + timedelta(days=1)

    updated = await feed_service.update_source(
        db_session,
        source,
        {"url": "https://feeds.example.test/replaced.txt"},
        actor,
    )
    assert updated.next_sync_at is not None
    assert updated.next_sync_at < utc_now() + timedelta(minutes=1)

    updated.enabled = False
    updated.next_sync_at = None
    reenabled = await feed_service.update_source(db_session, updated, {"enabled": True}, actor)

    assert reenabled.enabled is True
    assert reenabled.next_sync_at is not None
    assert reenabled.next_sync_at < utc_now() + timedelta(minutes=1)


async def test_update_interval_only_recomputes_due_from_now(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    source = await feed_service.create_source(db_session, payload(), actor)
    source.next_sync_at = utc_now() + timedelta(days=1)
    before = utc_now()

    updated = await feed_service.update_source(
        db_session,
        source,
        {"sync_interval_seconds": 7200},
        actor,
    )

    assert updated.sync_interval_seconds == 7200
    assert updated.next_sync_at is not None
    assert (
        before + timedelta(seconds=7199)
        <= updated.next_sync_at
        <= utc_now() + timedelta(seconds=7201)
    )


async def test_disable_clears_due_time_without_removing_assertions(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    source = await feed_service.create_source(db_session, payload(), actor)
    seed_run = FeedSyncRun(
        feed_source_id=source.id,
        source_name=source.name,
        sequence=100,
        trigger=ChangeTrigger.feed_manual,
    )
    db_session.add(seed_run)
    await db_session.flush()
    await feed_reconcile.reconcile(db_session, seed_run, parse_line_list(b"198.51.100.10\n"))

    disabled = await feed_service.update_source(db_session, source, {"enabled": False}, actor)

    assert disabled.next_sync_at is None
    assertion_count = await db_session.scalar(
        select(func.count())
        .select_from(BlacklistEntry)
        .where(
            BlacklistEntry.scope == BlacklistScope.global_,
            BlacklistEntry.source_cidr == "198.51.100.10/32",
        )
    )
    assert assertion_count == 1


async def test_manual_sync_is_allowed_while_source_is_disabled(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    source = await feed_service.create_source(db_session, payload(), actor)
    await feed_service.update_source(db_session, source, {"enabled": False}, actor)

    run = await feed_service.enqueue_sync(
        db_session,
        source,
        trigger=ChangeTrigger.feed_manual,
        dry_run=False,
        actor=actor,
    )

    assert run.trigger == ChangeTrigger.feed_manual
    assert (await source_job(db_session, run)).status == JobStatus.queued


async def test_enqueue_sync_creates_linked_run_job_and_increments_sequence(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    source = await feed_service.create_source(db_session, payload(), actor)

    run = await feed_service.enqueue_sync(
        db_session,
        source,
        trigger=ChangeTrigger.feed_dry_run,
        dry_run=True,
        actor=actor,
    )
    job = await source_job(db_session, run)

    assert (source.sync_sequence, run.sequence, run.dry_run) == (1, 1, True)
    assert (job.job_type, job.target_type, job.feed_sync_run_id, job.version) == (
        JobType.feed_sync,
        "feed_sync_run",
        run.id,
        1,
    )


async def test_competing_enqueues_join_the_existing_inflight_run(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        actor = await create_admin(db)
        source = await feed_service.create_source(db, payload(), actor)
        actor_id = actor.id
        source_id = source.id
        await db.commit()

    async def enqueue(trigger: ChangeTrigger) -> FeedSyncRun:
        async with committed_db() as db:
            source = await db.get(ThreatFeedSource, source_id)
            actor = await db.get(User, actor_id)
            assert source is not None
            run = await feed_service.enqueue_sync(
                db,
                source,
                trigger=trigger,
                dry_run=False,
                actor=actor,
            )
            await db.commit()
            return run

    manual, scheduled = await asyncio.gather(
        enqueue(ChangeTrigger.feed_manual),
        enqueue(ChangeTrigger.feed_schedule),
    )

    assert manual.id == scheduled.id
    async with committed_db() as db:
        run_count = await db.scalar(
            select(func.count())
            .select_from(FeedSyncRun)
            .where(FeedSyncRun.feed_source_id == source_id)
        )
        job_count = await db.scalar(
            select(func.count())
            .select_from(AgentJob)
            .join(FeedSyncRun, AgentJob.feed_sync_run_id == FeedSyncRun.id)
            .where(FeedSyncRun.feed_source_id == source_id, AgentJob.status == JobStatus.queued)
        )

    assert (run_count, job_count) == (1, 1)


async def test_list_due_sources_excludes_disabled_deleted_and_inflight_sources(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    due = await feed_service.create_source(db_session, payload(name="Due"), actor)
    disabled = await feed_service.create_source(db_session, payload(name="Disabled"), actor)
    deleted = await feed_service.create_source(db_session, payload(name="Deleted"), actor)
    inflight = await feed_service.create_source(db_session, payload(name="Inflight"), actor)
    now = utc_now()
    for source in (due, disabled, deleted, inflight):
        source.next_sync_at = now - timedelta(seconds=1)
    disabled.enabled = False
    deleted.deleted_at = now
    await feed_service.enqueue_sync(
        db_session,
        inflight,
        trigger=ChangeTrigger.feed_manual,
        dry_run=False,
        actor=actor,
    )

    sources = await feed_service.list_due_sources(db_session, now, limit=10)

    assert [source.id for source in sources] == [due.id]


async def test_sync_dispatch_happens_only_after_commit(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    async with committed_db() as db:
        actor = await create_admin(db)
        source = await feed_service.create_source(db, payload(), actor)
        run = await feed_service.enqueue_sync(
            db,
            source,
            trigger=ChangeTrigger.feed_manual,
            dry_run=False,
            actor=actor,
        )
        job = await source_job(db, run)

        assert await redis_client.lrange(APPLY_QUEUE_KEY, 0, -1) == []
        await db.commit()
        await run_post_commit_callbacks(db)

    assert await redis_client.lrange(APPLY_QUEUE_KEY, 0, -1) == [str(job.id)]


async def test_delete_tombstones_source_removes_only_its_assertions_and_audits(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    first = await feed_service.create_source(db_session, payload(name="First"), actor)
    second = await feed_service.create_source(db_session, payload(name="Second"), actor)
    first_seed = FeedSyncRun(
        feed_source_id=first.id,
        source_name=first.name,
        sequence=100,
        trigger=ChangeTrigger.feed_manual,
    )
    second_seed = FeedSyncRun(
        feed_source_id=second.id,
        source_name=second.name,
        sequence=100,
        trigger=ChangeTrigger.feed_manual,
    )
    db_session.add_all([first_seed, second_seed])
    await db_session.flush()
    await feed_reconcile.reconcile(db_session, first_seed, parse_line_list(b"198.51.100.20\n"))
    await feed_reconcile.reconcile(db_session, second_seed, parse_line_list(b"198.51.100.21\n"))

    delete_run = await feed_service.delete_source(db_session, first, actor)

    assert first.deleted_at is not None
    assert (first.enabled, first.next_sync_at) == (False, None)
    assert delete_run.trigger == ChangeTrigger.feed_delete
    assert (await source_job(db_session, delete_run)).job_type == JobType.feed_sync
    cidrs = list(
        (
            await db_session.scalars(
                select(BlacklistEntry.source_cidr)
                .where(BlacklistEntry.scope == BlacklistScope.global_)
                .order_by(BlacklistEntry.source_cidr)
            )
        ).all()
    )
    assert [str(cidr) for cidr in cidrs] == ["198.51.100.21/32"]
    audit = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "feed.delete"))
    ).scalar_one()
    assert audit.metadata_["dangerous"] is True


async def test_credential_reference_is_excluded_from_safe_record_and_audit_metadata(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session)
    source = await feed_service.create_source(
        db_session,
        payload(credential_env_var="THREAT_FEED_TOKEN"),
        actor,
    )
    record = feed_service.source_record(source)
    events = (await db_session.scalars(select(AuditEvent))).all()

    assert record.has_credential is True
    assert not hasattr(record, "credential_env_var")
    assert all("THREAT_FEED_TOKEN" not in str(event.metadata_) for event in events)
