import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feed_parser import ParseResult
from app.db.models import (
    ApplyStatus,
    AuditEvent,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    FeedSyncOverlap,
    FeedSyncRun,
    GlobalDenyState,
    ThreatFeedSource,
    utc_now,
)
from app.services.audit import record_event

MAX_GLOBAL_DENY_ENTRIES = 1_048_576
CANDIDATE_BATCH_SIZE = 1_000
OVERLAP_AUDIT_SAMPLE_LIMIT = 20
_CANDIDATE_TABLE = "feed_reconcile_candidate"
_OVERLAP_AUDIT_ACTION = "feed.sync.overlap"


class GlobalDenyLimitError(ValueError):
    pass


class GlobalDenyRevisionMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    valid: int
    added: int
    removed: int
    overlap_count: int
    global_changed: bool
    desired_revision: int | None
    noop: bool = False


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    cidrs: tuple[str, ...]
    digest: str
    changed: bool
    desired_revision: int


@dataclass(frozen=True, slots=True)
class GlobalDenySnapshot:
    revision: int
    digest: str
    cidrs: tuple[str, ...]


async def reconcile(
    db: AsyncSession,
    run: FeedSyncRun,
    parsed: ParseResult,
) -> ReconcileResult:
    """Replace one feed source's assertions without disturbing the global union."""
    async with db.begin_nested():
        source = await _locked_source(db, run.feed_source_id)
        if source is None or (
            source.deleted_at is not None and run.trigger != ChangeTrigger.feed_delete
        ):
            return ReconcileResult(
                valid=0,
                added=0,
                removed=0,
                overlap_count=0,
                global_changed=False,
                desired_revision=None,
                noop=True,
            )

        state = await _locked_global_state(db, create=not run.dry_run)
        await _load_candidates(db, parsed.cidrs)
        added, removed = await _source_delta_counts(db, source.id)
        overlap_count = await _overlap_count(db)
        before_cidrs = await _global_cidrs(db)

        if run.dry_run:
            effective_cidrs = await _prospective_global_cidrs(db, source.id)
            _enforce_capacity(effective_cidrs)
            _store_run_stats(
                run,
                parsed,
                added=added,
                removed=removed,
                overlap_count=overlap_count,
                global_changed=_digest(before_cidrs) != _digest(effective_cidrs),
                desired_revision=None,
            )
            await db.flush()
            return ReconcileResult(
                valid=parsed.valid_distinct_count,
                added=added,
                removed=removed,
                overlap_count=overlap_count,
                global_changed=_digest(before_cidrs) != _digest(effective_cidrs),
                desired_revision=None,
            )

        if state is None:
            raise RuntimeError("Global deny state was not initialized")

        now = utc_now()
        await _upsert_feed_entries(db, parsed.cidrs, now)
        await _replace_source_assertions(db, source.id, now)
        await _delete_orphaned_feed_entries(db)
        effective_cidrs = await _global_cidrs(db)
        _enforce_capacity(effective_cidrs)
        materialized = await _materialize_locked(db, state, effective_cidrs)
        global_changed = _digest(before_cidrs) != materialized.digest

        await _persist_overlaps(db, run.id, overlap_count, now)
        if overlap_count:
            await _record_overlap_summary(db, source, run, overlap_count)

        desired_revision = materialized.desired_revision if materialized.changed else None
        _store_run_stats(
            run,
            parsed,
            added=added,
            removed=removed,
            overlap_count=overlap_count,
            global_changed=global_changed,
            desired_revision=desired_revision,
        )
        await db.flush()
        return ReconcileResult(
            valid=parsed.valid_distinct_count,
            added=added,
            removed=removed,
            overlap_count=overlap_count,
            global_changed=global_changed,
            desired_revision=desired_revision,
        )


async def materialize_global_union(db: AsyncSession) -> MaterializeResult:
    """Hash the effective global deny rows and advance desired state when it changed."""
    async with db.begin_nested():
        state = await _locked_global_state(db, create=True)
        if state is None:
            raise RuntimeError("Global deny state was not initialized")
        cidrs = await _global_cidrs(db)
        _enforce_capacity(cidrs)
        result = await _materialize_locked(db, state, cidrs)
        await db.flush()
        return result


async def load_global_snapshot(
    db: AsyncSession,
    expected_revision: int,
) -> GlobalDenySnapshot:
    """Load a stable, revision-guarded global deny snapshot for the applier."""
    state = await _locked_global_state(db, create=False)
    if state is None or state.desired_revision != expected_revision:
        raise GlobalDenyRevisionMismatch(
            "Global deny revision no longer matches the requested snapshot"
        )

    cidrs = await _global_cidrs(db)
    digest = _digest(cidrs)
    if state.desired_digest != digest:
        raise GlobalDenyRevisionMismatch("Global deny rows no longer match the desired revision")
    return GlobalDenySnapshot(revision=state.desired_revision, digest=digest, cidrs=cidrs)


async def _locked_source(db: AsyncSession, source_id: uuid.UUID) -> ThreatFeedSource | None:
    return (
        (
            await db.execute(
                select(ThreatFeedSource).where(ThreatFeedSource.id == source_id).with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _locked_global_state(
    db: AsyncSession,
    *,
    create: bool,
) -> GlobalDenyState | None:
    if create:
        await db.execute(
            pg_insert(GlobalDenyState)
            .values(
                id=1,
                desired_revision=0,
                active_revision=0,
                apply_status=ApplyStatus.pending,
                updated_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=[GlobalDenyState.id])
        )
    return (
        (await db.execute(select(GlobalDenyState).where(GlobalDenyState.id == 1).with_for_update()))
        .scalars()
        .one_or_none()
    )


async def _load_candidates(db: AsyncSession, cidrs: tuple[str, ...]) -> None:
    await db.execute(
        text(
            f"CREATE TEMPORARY TABLE IF NOT EXISTS {_CANDIDATE_TABLE} ("
            "source_cidr cidr PRIMARY KEY"
            ") ON COMMIT DROP"
        )
    )
    await db.execute(text(f"DELETE FROM {_CANDIDATE_TABLE}"))
    statement = text(
        f"INSERT INTO {_CANDIDATE_TABLE} (source_cidr) VALUES (:source_cidr) ON CONFLICT DO NOTHING"
    )
    for batch in _batches(cidrs):
        await db.execute(statement, [{"source_cidr": cidr} for cidr in batch])


async def _source_delta_counts(db: AsyncSession, source_id: uuid.UUID) -> tuple[int, int]:
    added = (
        await db.execute(
            text(
                f"""
                SELECT count(*)
                FROM {_CANDIDATE_TABLE} candidate
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM feed_blacklist_assertion assertion
                    JOIN blacklist_entry entry ON entry.id = assertion.blacklist_entry_id
                    WHERE assertion.feed_source_id = :source_id
                      AND entry.scope = 'global'
                      AND entry.source_cidr = candidate.source_cidr
                )
                """
            ),
            {"source_id": source_id},
        )
    ).scalar_one()
    removed = (
        await db.execute(
            text(
                f"""
                SELECT count(*)
                FROM feed_blacklist_assertion assertion
                JOIN blacklist_entry entry ON entry.id = assertion.blacklist_entry_id
                WHERE assertion.feed_source_id = :source_id
                  AND entry.scope = 'global'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {_CANDIDATE_TABLE} candidate
                      WHERE candidate.source_cidr = entry.source_cidr
                  )
                """
            ),
            {"source_id": source_id},
        )
    ).scalar_one()
    return int(added), int(removed)


async def _overlap_count(db: AsyncSession) -> int:
    count = (
        await db.execute(
            text(
                f"""
                SELECT count(*)
                FROM {_CANDIDATE_TABLE} candidate
                JOIN whitelist_entry whitelist ON candidate.source_cidr && whitelist.source_cidr
                """
            )
        )
    ).scalar_one()
    return int(count)


async def _upsert_feed_entries(
    db: AsyncSession,
    cidrs: tuple[str, ...],
    now: datetime,
) -> None:
    for batch in _batches(cidrs):
        await db.execute(
            pg_insert(BlacklistEntry)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "scope": BlacklistScope.global_,
                        "source": BlacklistSource.feed,
                        "source_cidr": cidr,
                        "created_at": now,
                    }
                    for cidr in batch
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[BlacklistEntry.source_cidr],
                index_where=text("scope = 'global'"),
            )
        )


async def _replace_source_assertions(
    db: AsyncSession,
    source_id: uuid.UUID,
    now: datetime,
) -> None:
    await db.execute(
        text(
            f"""
            DELETE FROM feed_blacklist_assertion assertion
            USING blacklist_entry entry
            WHERE assertion.blacklist_entry_id = entry.id
              AND assertion.feed_source_id = :source_id
              AND entry.scope = 'global'
              AND NOT EXISTS (
                  SELECT 1
                  FROM {_CANDIDATE_TABLE} candidate
                  WHERE candidate.source_cidr = entry.source_cidr
              )
            """
        ),
        {"source_id": source_id},
    )
    await db.execute(
        text(
            f"""
            INSERT INTO feed_blacklist_assertion (
                feed_source_id,
                blacklist_entry_id,
                first_seen_at,
                last_seen_at
            )
            SELECT :source_id, entry.id, :now, :now
            FROM {_CANDIDATE_TABLE} candidate
            JOIN blacklist_entry entry
              ON entry.scope = 'global'
             AND entry.source_cidr = candidate.source_cidr
            ON CONFLICT (feed_source_id, blacklist_entry_id)
            DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at
            """
        ),
        {"source_id": source_id, "now": now},
    )


async def _delete_orphaned_feed_entries(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            DELETE FROM blacklist_entry entry
            WHERE entry.scope = 'global'
              AND entry.source = 'feed'
              AND NOT EXISTS (
                  SELECT 1
                  FROM feed_blacklist_assertion assertion
                  WHERE assertion.blacklist_entry_id = entry.id
              )
            """
        )
    )


async def _prospective_global_cidrs(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> tuple[str, ...]:
    rows = await db.execute(
        text(
            f"""
            SELECT effective.source_cidr
            FROM (
                SELECT entry.source_cidr::text AS source_cidr
                FROM blacklist_entry entry
                WHERE entry.scope = 'global'
                  AND NOT (
                      entry.source = 'feed'
                      AND EXISTS (
                          SELECT 1
                          FROM feed_blacklist_assertion assertion
                          WHERE assertion.blacklist_entry_id = entry.id
                            AND assertion.feed_source_id = :source_id
                            AND NOT EXISTS (
                                SELECT 1
                                FROM {_CANDIDATE_TABLE} candidate
                                WHERE candidate.source_cidr = entry.source_cidr
                            )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM feed_blacklist_assertion other_assertion
                          WHERE other_assertion.blacklist_entry_id = entry.id
                            AND other_assertion.feed_source_id <> :source_id
                      )
                  )
                UNION
                SELECT candidate.source_cidr::text AS source_cidr
                FROM {_CANDIDATE_TABLE} candidate
            ) effective
            ORDER BY effective.source_cidr
            """
        ),
        {"source_id": source_id},
    )
    return tuple(str(row[0]) for row in rows)


async def _global_cidrs(db: AsyncSession) -> tuple[str, ...]:
    cidrs = (
        await db.execute(
            select(BlacklistEntry.source_cidr)
            .where(BlacklistEntry.scope == BlacklistScope.global_)
            .order_by(BlacklistEntry.source_cidr)
        )
    ).scalars()
    return tuple(str(cidr) for cidr in cidrs)


async def _materialize_locked(
    db: AsyncSession,
    state: GlobalDenyState,
    cidrs: tuple[str, ...],
) -> MaterializeResult:
    digest = _digest(cidrs)
    changed = state.desired_digest != digest
    if changed:
        state.desired_digest = digest
        state.desired_revision += 1
        state.apply_status = ApplyStatus.pending
        state.updated_at = utc_now()
        await db.flush()
    return MaterializeResult(
        cidrs=cidrs,
        digest=digest,
        changed=changed,
        desired_revision=state.desired_revision,
    )


async def _persist_overlaps(
    db: AsyncSession,
    run_id: uuid.UUID,
    overlap_count: int,
    now: datetime,
) -> None:
    if overlap_count == 0:
        return
    result = await db.stream(
        text(
            f"""
            SELECT
                candidate.source_cidr::text,
                whitelist.id,
                whitelist.service_id
            FROM {_CANDIDATE_TABLE} candidate
            JOIN whitelist_entry whitelist ON candidate.source_cidr && whitelist.source_cidr
            ORDER BY candidate.source_cidr, whitelist.id
            """
        )
    )
    async for rows in result.partitions(CANDIDATE_BATCH_SIZE):
        await db.execute(
            pg_insert(FeedSyncOverlap)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "feed_sync_run_id": run_id,
                        "feed_source_cidr": str(row[0]),
                        "whitelist_entry_id": row[1],
                        "service_id": row[2],
                        "created_at": now,
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_nothing(constraint="uq_feed_sync_overlap_run_cidr_whitelist")
        )


async def _record_overlap_summary(
    db: AsyncSession,
    source: ThreatFeedSource,
    run: FeedSyncRun,
    overlap_count: int,
) -> None:
    already_recorded = (
        await db.execute(
            select(AuditEvent.id)
            .where(
                AuditEvent.action == _OVERLAP_AUDIT_ACTION,
                AuditEvent.target_type == "feed_sync_run",
                AuditEvent.target_id == str(run.id),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if already_recorded is not None:
        return

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    candidate.source_cidr::text,
                    whitelist.id::text,
                    whitelist.service_id::text
                FROM {_CANDIDATE_TABLE} candidate
                JOIN whitelist_entry whitelist ON candidate.source_cidr && whitelist.source_cidr
                ORDER BY candidate.source_cidr, whitelist.id
                LIMIT :sample_limit
                """
            ),
            {"sample_limit": OVERLAP_AUDIT_SAMPLE_LIMIT},
        )
    ).all()
    samples = [
        {
            "feed_cidr": str(row[0]),
            "whitelist_entry_id": str(row[1]),
            "service_id": str(row[2]),
        }
        for row in rows
    ]
    await record_event(
        db,
        actor=None,
        action=_OVERLAP_AUDIT_ACTION,
        target_type="feed_sync_run",
        target_id=str(run.id),
        outcome="warning",
        metadata={
            "source_id": str(source.id),
            "overlap_count": overlap_count,
            "samples": samples,
        },
    )


def _store_run_stats(
    run: FeedSyncRun,
    parsed: ParseResult,
    *,
    added: int,
    removed: int,
    overlap_count: int,
    global_changed: bool,
    desired_revision: int | None,
) -> None:
    run.valid = parsed.valid_distinct_count
    run.duplicates = parsed.duplicate_count
    run.skipped_invalid = parsed.invalid_count
    run.added = added
    run.removed = removed
    run.overlap_count = overlap_count
    run.global_changed = global_changed
    run.desired_revision = desired_revision


def _enforce_capacity(cidrs: tuple[str, ...]) -> None:
    if len(cidrs) > MAX_GLOBAL_DENY_ENTRIES:
        raise GlobalDenyLimitError(
            f"Global deny entry limit of {MAX_GLOBAL_DENY_ENTRIES} would be exceeded"
        )


def _digest(cidrs: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(cidrs).encode("utf-8")).hexdigest()


def _batches(cidrs: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    for start in range(0, len(cidrs), CANDIDATE_BATCH_SIZE):
        yield cidrs[start : start + CANDIDATE_BATCH_SIZE]
