import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentJob,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    FeedBlacklistAssertion,
    FeedSyncOverlap,
    FeedSyncRun,
    GlobalDenyState,
    JobType,
    ProtectedService,
    Tenant,
    ThreatFeedSource,
    WhitelistEntry,
    utc_now,
)

pytestmark = pytest.mark.integration


async def create_source(
    db_session: AsyncSession,
    *,
    name: str = "Example Feed",
) -> ThreatFeedSource:
    source = ThreatFeedSource(
        name=name,
        url="https://feeds.example.test/deny.txt",
        sync_interval_seconds=3600,
    )
    db_session.add(source)
    await db_session.flush()
    return source


async def create_run(db_session: AsyncSession, source: ThreatFeedSource) -> FeedSyncRun:
    run = FeedSyncRun(
        feed_source_id=source.id,
        source_name=source.name,
        sequence=1,
        trigger=ChangeTrigger.feed_manual,
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def create_service(db_session: AsyncSession) -> ProtectedService:
    tenant = Tenant(name="Feed Model Tenant")
    service = ProtectedService(
        tenant=tenant,
        name="edge",
        cidr_or_ip="203.0.113.10/32",
    )
    db_session.add_all([tenant, service])
    await db_session.flush()
    return service


async def test_source_name_is_case_insensitive_and_interval_is_bounded(
    db_session: AsyncSession,
) -> None:
    await create_source(db_session, name="Abuse Feed")

    with pytest.raises(IntegrityError, match="uq_threat_feed_source_name"):
        async with db_session.begin_nested():
            db_session.add(
                ThreatFeedSource(
                    name="abuse feed",
                    url="https://feeds.example.test/other.txt",
                    sync_interval_seconds=3600,
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError, match="ck_threat_feed_source_sync_interval"):
        async with db_session.begin_nested():
            db_session.add(
                ThreatFeedSource(
                    name="Short Interval",
                    url="https://feeds.example.test/short.txt",
                    sync_interval_seconds=299,
                )
            )
            await db_session.flush()


async def test_feed_run_sequence_is_unique_per_source(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    await create_run(db_session, source)

    with pytest.raises(IntegrityError, match="uq_feed_sync_run_source_sequence"):
        async with db_session.begin_nested():
            db_session.add(
                FeedSyncRun(
                    feed_source_id=source.id,
                    source_name=source.name,
                    sequence=1,
                    trigger=ChangeTrigger.feed_schedule,
                )
            )
            await db_session.flush()


async def test_tombstoned_source_retains_its_sync_runs(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run = await create_run(db_session, source)

    source.deleted_at = utc_now()
    await db_session.flush()

    assert source.deleted_at is not None
    run_record = (
        await db_session.execute(select(FeedSyncRun).where(FeedSyncRun.id == run.id))
    ).scalar_one()
    assert run_record.id == run.id

    with pytest.raises(IntegrityError, match="feed_sync_run_feed_source_id_fkey"):
        async with db_session.begin_nested():
            await db_session.delete(source)
            await db_session.flush()


async def test_blacklist_entry_deletion_cascades_feed_assertions(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    entry = BlacklistEntry(
        scope=BlacklistScope.global_,
        source=BlacklistSource.feed,
        source_cidr="198.51.100.0/24",
    )
    db_session.add(entry)
    await db_session.flush()
    db_session.add(
        FeedBlacklistAssertion(
            feed_source_id=source.id,
            blacklist_entry_id=entry.id,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
        )
    )
    await db_session.flush()

    await db_session.delete(entry)
    await db_session.flush()

    count = (
        await db_session.execute(select(func.count(FeedBlacklistAssertion.feed_source_id)))
    ).scalar_one()
    assert count == 0


async def test_feed_overlap_is_unique_and_cascades_with_run(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    whitelist = WhitelistEntry(service_id=service.id, source_cidr="198.51.100.0/24")
    db_session.add(whitelist)
    await db_session.flush()
    source = await create_source(db_session)
    run = await create_run(db_session, source)
    overlap = FeedSyncOverlap(
        feed_sync_run_id=run.id,
        feed_source_cidr="198.51.100.128/25",
        whitelist_entry_id=whitelist.id,
        service_id=service.id,
    )
    db_session.add(overlap)
    await db_session.flush()

    with pytest.raises(IntegrityError, match="uq_feed_sync_overlap_run_cidr_whitelist"):
        async with db_session.begin_nested():
            db_session.add(
                FeedSyncOverlap(
                    feed_sync_run_id=run.id,
                    feed_source_cidr="198.51.100.128/25",
                    whitelist_entry_id=whitelist.id,
                    service_id=service.id,
                )
            )
            await db_session.flush()

    await db_session.delete(run)
    await db_session.flush()

    assert (await db_session.execute(select(func.count(FeedSyncOverlap.id)))).scalar_one() == 0


async def test_feed_indexes_include_whitelist_gist_and_assertion_reverse_index(
    db_session: AsyncSession,
) -> None:
    indexes = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN (
                      'ix_whitelist_entry_source_cidr_gist',
                      'ix_feed_blacklist_assertion_blacklist_entry_id'
                  )
                ORDER BY indexname
                """
            )
        )
    ).all()

    assert indexes[0][0] == "ix_feed_blacklist_assertion_blacklist_entry_id"
    assert indexes[1][0] == "ix_whitelist_entry_source_cidr_gist"
    assert "USING gist (source_cidr inet_ops)" in indexes[1][1]


async def test_global_deny_state_initializes_exactly_one_row(db_session: AsyncSession) -> None:
    state = GlobalDenyState()
    db_session.add(state)
    await db_session.flush()

    assert state.id == 1
    assert state.desired_revision == 0
    assert state.active_revision == 0

    with pytest.raises(IntegrityError, match="ck_global_deny_state_singleton"):
        async with db_session.begin_nested():
            db_session.add(GlobalDenyState(id=2))
            await db_session.flush()


async def test_agent_jobs_accept_each_typed_target_shape(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    source = await create_source(db_session)
    run = await create_run(db_session, source)
    db_session.add_all(
        [
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=1,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
            ),
            AgentJob(
                target_type="feed_sync_run",
                target_id=None,
                feed_sync_run_id=run.id,
                version=1,
                job_type=JobType.feed_sync,
                trigger=ChangeTrigger.feed_manual,
            ),
            AgentJob(
                target_type="global_deny",
                target_id=None,
                feed_sync_run_id=None,
                version=1,
                job_type=JobType.global_deny_apply,
                trigger=ChangeTrigger.global_deny_retry,
            ),
        ]
    )

    await db_session.flush()


@pytest.mark.parametrize(
    ("job_type", "target_type", "has_service_target", "has_feed_run"),
    [
        (JobType.service_update, "service", False, False),
        (JobType.feed_sync, "feed_sync_run", True, True),
        (JobType.global_deny_apply, "global_deny", False, True),
    ],
)
async def test_agent_jobs_reject_invalid_typed_target_shapes(
    db_session: AsyncSession,
    job_type: JobType,
    target_type: str,
    has_service_target: bool,
    has_feed_run: bool,
) -> None:
    service = await create_service(db_session)
    source = await create_source(db_session)
    run = await create_run(db_session, source)

    with pytest.raises(IntegrityError, match="ck_agent_job_target_shape"):
        async with db_session.begin_nested():
            db_session.add(
                AgentJob(
                    target_type=target_type,
                    target_id=service.id if has_service_target else None,
                    feed_sync_run_id=run.id if has_feed_run else None,
                    version=1,
                    job_type=job_type,
                    trigger=ChangeTrigger.service,
                )
            )
            await db_session.flush()


async def test_agent_job_partial_indexes_preserve_typed_idempotency(
    db_session: AsyncSession,
) -> None:
    service = await create_service(db_session)
    source = await create_source(db_session)
    run = await create_run(db_session, source)
    db_session.add_all(
        [
            AgentJob(
                target_type="service",
                target_id=service.id,
                version=9,
                job_type=JobType.service_update,
                trigger=ChangeTrigger.service,
            ),
            AgentJob(
                target_type="feed_sync_run",
                feed_sync_run_id=run.id,
                version=2,
                job_type=JobType.feed_sync,
                trigger=ChangeTrigger.feed_manual,
            ),
            AgentJob(
                target_type="global_deny",
                version=3,
                job_type=JobType.global_deny_apply,
                trigger=ChangeTrigger.global_deny_retry,
            ),
        ]
    )
    await db_session.flush()

    duplicates = [
        AgentJob(
            target_type="service",
            target_id=service.id,
            version=9,
            job_type=JobType.service_update,
            trigger=ChangeTrigger.service,
        ),
        AgentJob(
            target_type="feed_sync_run",
            feed_sync_run_id=run.id,
            version=4,
            job_type=JobType.feed_sync,
            trigger=ChangeTrigger.feed_schedule,
        ),
        AgentJob(
            target_type="global_deny",
            version=3,
            job_type=JobType.global_deny_apply,
            trigger=ChangeTrigger.global_deny_retry,
        ),
    ]

    for duplicate, index_name in zip(
        duplicates,
        (
            "uq_agent_job_service_target_version",
            "uq_agent_job_feed_sync_run",
            "uq_agent_job_global_deny_revision",
        ),
        strict=True,
    ):
        with pytest.raises(IntegrityError, match=index_name):
            async with db_session.begin_nested():
                db_session.add(duplicate)
                await db_session.flush()


async def test_feed_sync_run_foreign_key_cascades_to_linked_agent_job(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session)
    run = await create_run(db_session, source)
    job = AgentJob(
        target_type="feed_sync_run",
        feed_sync_run_id=run.id,
        version=1,
        job_type=JobType.feed_sync,
        trigger=ChangeTrigger.feed_manual,
    )
    db_session.add(job)
    await db_session.flush()

    await db_session.delete(run)
    await db_session.flush()

    job_record = (
        await db_session.execute(select(AgentJob).where(AgentJob.id == job.id))
    ).scalar_one_or_none()
    assert job_record is None
