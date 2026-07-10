import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.feed_parser import parse_line_list
from app.db.models import (
    ApplyStatus,
    AuditEvent,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    FeedBlacklistAssertion,
    FeedSyncOverlap,
    FeedSyncRun,
    GlobalDenyState,
    ProtectedService,
    Tenant,
    ThreatFeedSource,
    WhitelistEntry,
    utc_now,
)
from app.services import feed_reconcile
from app.services.feed_reconcile import (
    GlobalDenyLimitError,
    GlobalDenyRevisionMismatch,
    load_global_snapshot,
    materialize_global_union,
    reconcile,
)

pytestmark = pytest.mark.integration


async def create_source(db: AsyncSession, name: str) -> ThreatFeedSource:
    source = ThreatFeedSource(
        name=name,
        url=f"https://feeds.example.test/{name}.txt",
        sync_interval_seconds=3600,
    )
    db.add(source)
    await db.flush()
    return source


async def create_run(
    db: AsyncSession,
    source: ThreatFeedSource,
    *,
    sequence: int = 1,
    dry_run: bool = False,
) -> FeedSyncRun:
    run = FeedSyncRun(
        feed_source_id=source.id,
        source_name=source.name,
        sequence=sequence,
        trigger=ChangeTrigger.feed_dry_run if dry_run else ChangeTrigger.feed_manual,
        dry_run=dry_run,
    )
    db.add(run)
    await db.flush()
    return run


async def create_whitelist(db: AsyncSession, source_cidr: str) -> WhitelistEntry:
    tenant = Tenant(name=f"Feed Reconcile Tenant {uuid.uuid4()}")
    service = ProtectedService(
        tenant=tenant,
        name=f"edge-{uuid.uuid4()}",
        cidr_or_ip="203.0.113.10/32",
    )
    entry = WhitelistEntry(service=service, source_cidr=source_cidr)
    db.add_all([tenant, service, entry])
    await db.flush()
    return entry


async def global_cidrs(db: AsyncSession) -> list[str]:
    cidrs = (
        await db.execute(
            select(BlacklistEntry.source_cidr)
            .where(BlacklistEntry.scope == BlacklistScope.global_)
            .order_by(BlacklistEntry.source_cidr)
        )
    ).scalars()
    return [str(cidr) for cidr in cidrs]


async def source_cidrs(db: AsyncSession, source: ThreatFeedSource) -> list[str]:
    cidrs = (
        await db.execute(
            select(BlacklistEntry.source_cidr)
            .join(
                FeedBlacklistAssertion,
                FeedBlacklistAssertion.blacklist_entry_id == BlacklistEntry.id,
            )
            .where(FeedBlacklistAssertion.feed_source_id == source.id)
            .order_by(BlacklistEntry.source_cidr)
        )
    ).scalars()
    return [str(cidr) for cidr in cidrs]


async def test_reconcile_replaces_only_target_source_assertions_and_counts_deltas(
    db_session: AsyncSession,
) -> None:
    first = await create_source(db_session, "First Feed")
    second = await create_source(db_session, "Second Feed")

    first_run = await create_run(db_session, first)
    first_result = await reconcile(
        db_session,
        first_run,
        parse_line_list(b"198.51.100.1\n198.51.100.2\n"),
    )
    second_run = await create_run(db_session, second)
    await reconcile(
        db_session,
        second_run,
        parse_line_list(b"198.51.100.2\n198.51.100.3\n"),
    )
    replacement_run = await create_run(db_session, first, sequence=2)
    replacement_result = await reconcile(
        db_session,
        replacement_run,
        parse_line_list(b"198.51.100.2\n"),
    )

    assert (first_result.added, first_result.removed, first_result.global_changed) == (2, 0, True)
    assert (replacement_result.added, replacement_result.removed) == (0, 1)
    assert await source_cidrs(db_session, first) == ["198.51.100.2/32"]
    assert await source_cidrs(db_session, second) == ["198.51.100.2/32", "198.51.100.3/32"]
    assert await global_cidrs(db_session) == ["198.51.100.2/32", "198.51.100.3/32"]


async def test_reconcile_manual_global_row_is_never_overwritten_or_deleted(
    db_session: AsyncSession,
) -> None:
    manual = BlacklistEntry(
        scope=BlacklistScope.global_,
        source=BlacklistSource.manual,
        source_cidr="198.51.100.10/32",
    )
    db_session.add(manual)
    source = await create_source(db_session, "Manual Precedence Feed")
    await db_session.flush()

    await reconcile(
        db_session,
        await create_run(db_session, source),
        parse_line_list(b"198.51.100.10\n"),
    )
    await reconcile(
        db_session,
        await create_run(db_session, source, sequence=2),
        parse_line_list(b"198.51.100.11\n"),
    )

    await db_session.refresh(manual)
    assert manual.source == BlacklistSource.manual
    assert await global_cidrs(db_session) == ["198.51.100.10/32", "198.51.100.11/32"]
    assert await source_cidrs(db_session, source) == ["198.51.100.11/32"]


async def test_source_only_assertion_change_does_not_advance_desired_revision(
    db_session: AsyncSession,
) -> None:
    first = await create_source(db_session, "Revision First Feed")
    second = await create_source(db_session, "Revision Second Feed")
    await reconcile(
        db_session,
        await create_run(db_session, first),
        parse_line_list(b"198.51.100.20\n"),
    )
    state = (await db_session.execute(select(GlobalDenyState))).scalar_one()
    first_revision = state.desired_revision

    result = await reconcile(
        db_session,
        await create_run(db_session, second),
        parse_line_list(b"198.51.100.20\n"),
    )

    assert result.global_changed is False
    assert result.desired_revision is None
    assert state.desired_revision == first_revision
    assert await global_cidrs(db_session) == ["198.51.100.20/32"]


async def test_byte_identical_reconcile_is_a_revision_noop(db_session: AsyncSession) -> None:
    source = await create_source(db_session, "Noop Feed")
    await reconcile(
        db_session,
        await create_run(db_session, source),
        parse_line_list(b"198.51.100.30\n198.51.100.31\n"),
    )
    state = (await db_session.execute(select(GlobalDenyState))).scalar_one()
    revision = state.desired_revision

    result = await reconcile(
        db_session,
        await create_run(db_session, source, sequence=2),
        parse_line_list(b"198.51.100.30\n198.51.100.31\n"),
    )

    assert (result.added, result.removed, result.global_changed, result.desired_revision) == (
        0,
        0,
        False,
        None,
    )
    assert state.desired_revision == revision


async def test_dry_run_reports_counts_and_overlaps_without_list_state_mutation(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session, "Dry Run Feed")
    await reconcile(
        db_session,
        await create_run(db_session, source),
        parse_line_list(b"198.51.100.40\n"),
    )
    await create_whitelist(db_session, "198.51.100.128/25")
    state = (await db_session.execute(select(GlobalDenyState))).scalar_one()
    digest = state.desired_digest
    revision = state.desired_revision
    dry_run = await create_run(db_session, source, sequence=2, dry_run=True)

    result = await reconcile(
        db_session,
        dry_run,
        parse_line_list(b"198.51.100.129\n"),
    )

    assert (result.added, result.removed, result.overlap_count) == (1, 1, 1)
    assert await source_cidrs(db_session, source) == ["198.51.100.40/32"]
    assert await global_cidrs(db_session) == ["198.51.100.40/32"]
    assert state.desired_digest == digest
    assert state.desired_revision == revision
    assert dry_run.valid == 1
    assert dry_run.overlap_count == 1
    assert dry_run.desired_revision is None
    assert (await db_session.execute(select(func.count(FeedSyncOverlap.id)))).scalar_one() == 0
    assert (
        await db_session.execute(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == "feed.sync.overlap")
        )
    ).scalar_one() == 0


async def test_equal_overlap_persists_pair_and_bounded_credential_free_audit_summary(
    db_session: AsyncSession,
) -> None:
    whitelist = await create_whitelist(db_session, "198.51.100.50/32")
    source = await create_source(db_session, "Equal Overlap Feed")
    run = await create_run(db_session, source)

    result = await reconcile(db_session, run, parse_line_list(b"198.51.100.50\n"))

    overlap = (await db_session.execute(select(FeedSyncOverlap))).scalar_one()
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "feed.sync.overlap"))
    ).scalar_one()
    assert result.overlap_count == 1
    assert run.overlap_count == 1
    assert overlap.feed_sync_run_id == run.id
    assert str(overlap.feed_source_cidr) == "198.51.100.50/32"
    assert overlap.whitelist_entry_id == whitelist.id
    assert event.target_id == str(run.id)
    assert event.metadata_["source_id"] == str(source.id)
    assert event.metadata_["overlap_count"] == 1
    assert len(event.metadata_["samples"]) <= feed_reconcile.OVERLAP_AUDIT_SAMPLE_LIMIT
    assert "credential" not in str(event.metadata_).lower()
    assert await global_cidrs(db_session) == ["198.51.100.50/32"]


async def test_contained_and_containing_overlaps_persist_each_pair_but_disjoint_does_not(
    db_session: AsyncSession,
) -> None:
    await create_whitelist(db_session, "198.51.100.128/25")
    source = await create_source(db_session, "Range Overlap Feed")
    run = await create_run(db_session, source)

    result = await reconcile(
        db_session,
        run,
        parse_line_list(b"198.51.100.0/24\n198.51.100.128/26\n192.0.2.1\n"),
    )

    overlaps = [
        str(cidr)
        for cidr in (
            await db_session.execute(
                select(FeedSyncOverlap.feed_source_cidr).order_by(FeedSyncOverlap.feed_source_cidr)
            )
        ).scalars()
    ]
    assert result.overlap_count == 2
    assert overlaps == ["198.51.100.0/24", "198.51.100.128/26"]
    assert await global_cidrs(db_session) == [
        "192.0.2.1/32",
        "198.51.100.0/24",
        "198.51.100.128/26",
    ]


async def test_global_capacity_limit_rolls_back_reconcile(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await create_source(db_session, "Capacity Feed")
    run = await create_run(db_session, source)
    monkeypatch.setattr(feed_reconcile, "MAX_GLOBAL_DENY_ENTRIES", 1)

    with pytest.raises(GlobalDenyLimitError):
        async with db_session.begin_nested():
            await reconcile(db_session, run, parse_line_list(b"198.51.100.60\n198.51.100.61\n"))

    assert await global_cidrs(db_session) == []
    assert await source_cidrs(db_session, source) == []
    assert (await db_session.execute(select(func.count(GlobalDenyState.id)))).scalar_one() == 0


async def test_deleted_source_ordinary_sync_is_a_safe_noop(db_session: AsyncSession) -> None:
    source = await create_source(db_session, "Deleted Feed")
    source.deleted_at = utc_now()
    run = await create_run(db_session, source)

    result = await reconcile(db_session, run, parse_line_list(b"198.51.100.70\n"))

    assert result.noop is True
    assert await global_cidrs(db_session) == []
    assert (await db_session.execute(select(func.count(GlobalDenyState.id)))).scalar_one() == 0


async def test_materialize_global_union_hashes_sorted_cidrs_and_only_advances_on_change(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            BlacklistEntry(
                scope=BlacklistScope.global_,
                source=BlacklistSource.manual,
                source_cidr="198.51.100.81/32",
            ),
            BlacklistEntry(
                scope=BlacklistScope.global_,
                source=BlacklistSource.manual,
                source_cidr="198.51.100.80/32",
            ),
        ]
    )
    await db_session.flush()

    first = await materialize_global_union(db_session)
    second = await materialize_global_union(db_session)
    state = (await db_session.execute(select(GlobalDenyState))).scalar_one()

    assert first.cidrs == ("198.51.100.80/32", "198.51.100.81/32")
    assert first.changed is True
    assert second.changed is False
    assert second.desired_revision == first.desired_revision
    assert state.apply_status == ApplyStatus.pending


async def test_load_global_snapshot_requires_matching_revision_and_returns_sorted_cidrs(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            BlacklistEntry(
                scope=BlacklistScope.global_,
                source=BlacklistSource.manual,
                source_cidr="198.51.100.91/32",
            ),
            BlacklistEntry(
                scope=BlacklistScope.global_,
                source=BlacklistSource.manual,
                source_cidr="198.51.100.90/32",
            ),
        ]
    )
    await db_session.flush()
    materialized = await materialize_global_union(db_session)

    snapshot = await load_global_snapshot(db_session, materialized.desired_revision)

    assert snapshot.revision == materialized.desired_revision
    assert snapshot.digest == materialized.digest
    assert snapshot.cidrs == ("198.51.100.90/32", "198.51.100.91/32")
    with pytest.raises(GlobalDenyRevisionMismatch):
        await load_global_snapshot(db_session, materialized.desired_revision + 1)


async def test_orphaned_feed_row_is_removed_without_deleting_manual_row(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session, "Orphan Cleanup Feed")
    manual = BlacklistEntry(
        scope=BlacklistScope.global_,
        source=BlacklistSource.manual,
        source_cidr="198.51.100.100/32",
    )
    db_session.add(manual)
    await db_session.flush()
    await reconcile(
        db_session,
        await create_run(db_session, source),
        parse_line_list(b"198.51.100.101\n"),
    )
    await reconcile(
        db_session,
        await create_run(db_session, source, sequence=2),
        parse_line_list(b"198.51.100.102\n"),
    )

    assert await global_cidrs(db_session) == ["198.51.100.100/32", "198.51.100.102/32"]
    await db_session.refresh(manual)
    assert manual.source == BlacklistSource.manual


async def test_reconcile_loads_more_than_one_bounded_candidate_batch(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session, "Batch Feed")
    parsed = parse_line_list(
        "\n".join(f"10.0.{index // 256}.{index % 256}" for index in range(1001)).encode()
    )

    result = await reconcile(db_session, await create_run(db_session, source), parsed)

    assert result.valid == 1001
    assert result.added == 1001
    assert (await db_session.execute(select(func.count(BlacklistEntry.id)))).scalar_one() == 1001


async def test_concurrent_source_reconciles_preserve_the_global_union(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as setup:
        first = await create_source(setup, "Concurrent First Feed")
        second = await create_source(setup, "Concurrent Second Feed")
        first_run = await create_run(setup, first)
        second_run = await create_run(setup, second)
        first_id, second_id = first_run.id, second_run.id
        await setup.commit()

    async def reconcile_in_session(run_id: uuid.UUID, body: bytes) -> None:
        async with committed_db() as session:
            run = await session.get(FeedSyncRun, run_id)
            assert run is not None
            await reconcile(session, run, parse_line_list(body))
            await session.commit()

    await asyncio.gather(
        reconcile_in_session(first_id, b"198.51.100.110\n"),
        reconcile_in_session(second_id, b"198.51.100.111\n"),
    )

    async with committed_db() as verify:
        assert await global_cidrs(verify) == ["198.51.100.110/32", "198.51.100.111/32"]
        state = (await verify.execute(select(GlobalDenyState))).scalar_one()
        assert state.desired_revision == 2
