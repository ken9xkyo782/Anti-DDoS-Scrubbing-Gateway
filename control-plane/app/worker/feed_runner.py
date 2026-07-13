import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.feed_parser import ParseOutcome, ParseResult, parse_line_list
from app.db.models import (
    AgentJob,
    ApplyStatus,
    FeedSyncRun,
    FeedSyncStatus,
    GlobalDenyState,
    JobType,
    ThreatFeedSource,
)
from app.db.session import session_scope
from app.services.feed_fetch import fetch_line_list
from app.services.feed_reconcile import (
    GlobalDenySnapshot,
    ReconcileResult,
    load_global_snapshot,
    reconcile,
)
from app.worker.feed_coordinator import FeedFetchCompletion
from app.worker.feed_jobs import JOB_LIFECYCLES

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GlobalDenyApplyResult:
    active_slot: int
    node_map_version: int


class GlobalDenyApplier(Protocol):
    async def apply_global(self, snapshot: GlobalDenySnapshot) -> GlobalDenyApplyResult: ...


class FeedParseError(ValueError):
    """A credential-safe failure for a response with no usable feed entries."""


@dataclass(slots=True)
class _RunMetrics:
    fetched_lines: int = 0
    valid: int = 0
    duplicates: int = 0
    skipped_invalid: int = 0
    added: int = 0
    removed: int = 0
    overlap_count: int = 0
    global_changed: bool = False


@dataclass(slots=True)
class FeedRunner:
    """Run feed jobs with external work outside of database transactions."""

    client: httpx.AsyncClient
    settings: Settings
    global_applier: GlobalDenyApplier

    async def handle_feed_sync(self, job: AgentJob) -> None:
        """Run a feed synchronously for direct processor callers."""
        started = time.monotonic()
        source_id: uuid.UUID | None = None

        try:
            source = await self.load_source_for_fetch(job)
            if source is None:
                await self._finish_feed(job.id, FeedSyncStatus.success, node_map_version=None)
                self._log_summary(
                    source_id=None,
                    run_id=job.feed_sync_run_id,
                    metrics=_RunMetrics(),
                    duration_ms=_duration_ms(started),
                    status=FeedSyncStatus.success,
                )
                return
            source_id = source.id
            try:
                body = await self.fetch_source(source)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                completion = FeedFetchCompletion(
                    job=job,
                    source_id=source.id,
                    started=started,
                    body=None,
                    error=exc,
                )
            else:
                completion = FeedFetchCompletion(
                    job=job,
                    source_id=source.id,
                    started=started,
                    body=body,
                    error=None,
                )
            await self.complete_feed_fetch(completion)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail_feed(job.id, exc)
            self._log_summary(
                source_id=source_id,
                run_id=job.feed_sync_run_id,
                metrics=_RunMetrics(),
                duration_ms=_duration_ms(started),
                status=FeedSyncStatus.failed,
            )

    async def load_source_for_fetch(self, job: AgentJob) -> ThreatFeedSource | None:
        loaded = await self._load_run_and_source(job.feed_sync_run_id)
        if loaded is None:
            return None
        _, source = loaded
        return source

    async def finish_missing_feed(self, job: AgentJob) -> None:
        started = time.monotonic()
        await self._finish_feed(job.id, FeedSyncStatus.success, node_map_version=None)
        self._log_summary(
            source_id=None,
            run_id=job.feed_sync_run_id,
            metrics=_RunMetrics(),
            duration_ms=_duration_ms(started),
            status=FeedSyncStatus.success,
        )

    async def fetch_source(self, source: ThreatFeedSource) -> bytes:
        fetched = await fetch_line_list(source, self.client, self.settings)
        return fetched.body

    async def complete_feed_fetch(self, completion: FeedFetchCompletion) -> None:
        """Parse, reconcile, apply, and finish a completed network fetch."""
        metrics = _RunMetrics()
        status = FeedSyncStatus.failed
        job = completion.job

        try:
            if completion.error is not None:
                raise completion.error
            if completion.body is None:
                raise RuntimeError("Feed fetch completed without a response body")

            parsed = parse_line_list(completion.body)
            _set_parse_metrics(metrics, parsed)
            if parsed.outcome == ParseOutcome.failed:
                if job.feed_sync_run_id is not None:
                    await self._store_failed_parse_stats(job.feed_sync_run_id, parsed)
                raise FeedParseError(_parse_failure_message(parsed))

            if job.feed_sync_run_id is None:
                raise RuntimeError("Feed sync job is missing its run")
            result, desired_revision = await self._reconcile(job.feed_sync_run_id, parsed)
            _set_reconcile_metrics(metrics, result)
            node_map_version: int | None = None
            if desired_revision is not None:
                apply_result = await self._converge_desired_revision(desired_revision)
                if apply_result is not None:
                    node_map_version = apply_result.node_map_version

            status = (
                FeedSyncStatus.partial
                if parsed.outcome == ParseOutcome.partial
                else FeedSyncStatus.success
            )
            await self._finish_feed(job.id, status, node_map_version=node_map_version)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail_feed(job.id, exc)
        finally:
            self._log_summary(
                source_id=completion.source_id,
                run_id=job.feed_sync_run_id,
                metrics=metrics,
                duration_ms=_duration_ms(completion.started),
                status=status,
            )

    async def handle_global_deny_apply(self, job: AgentJob) -> None:
        """Converge a queued desired revision without fetching a feed source."""
        await self._converge_desired_revision(job.version)

    async def _load_run_and_source(
        self,
        run_id: uuid.UUID | None,
    ) -> tuple[FeedSyncRun, ThreatFeedSource] | None:
        if run_id is None:
            return None
        async with session_scope() as db:
            run = await db.get(FeedSyncRun, run_id)
            if run is None:
                return None
            source = await db.get(ThreatFeedSource, run.feed_source_id)
            if source is None or source.deleted_at is not None:
                return None
            return run, source

    async def _reconcile(
        self,
        run_id: uuid.UUID,
        parsed: ParseResult,
    ) -> tuple[ReconcileResult, int | None]:
        async with session_scope() as db:
            run = await db.get(FeedSyncRun, run_id)
            if run is None:
                raise RuntimeError("Feed sync run is missing")
            run.fetched_lines = parsed.physical_line_count
            result = await reconcile(db, run, parsed)
            if run.dry_run or result.noop:
                return result, None

            state = await db.get(GlobalDenyState, 1)
            desired_revision = (
                state.desired_revision if state is not None and _state_needs_apply(state) else None
            )
            return result, desired_revision

    async def _converge_desired_revision(
        self,
        expected_revision: int | None,
    ) -> GlobalDenyApplyResult | None:
        if expected_revision is None:
            return None

        async with session_scope() as db:
            state = await _locked_global_state(db)
            if (
                state is None
                or state.desired_revision != expected_revision
                or not _state_needs_apply(state)
            ):
                return None
            snapshot = await load_global_snapshot(db, expected_revision)

        result = await self.global_applier.apply_global(snapshot)

        async with session_scope() as db:
            state = await _locked_global_state(db)
            if (
                state is None
                or state.desired_revision != snapshot.revision
                or state.desired_digest != snapshot.digest
            ):
                return result
            state.active_revision = snapshot.revision
            state.active_digest = snapshot.digest
            state.apply_status = ApplyStatus.active
            state.last_error = None
            state.last_node_map_version = result.node_map_version
            await db.flush()
        return result

    async def _finish_feed(
        self,
        job_id: uuid.UUID,
        outcome: FeedSyncStatus,
        *,
        node_map_version: int | None,
    ) -> None:
        async with session_scope() as db:
            job = await db.get(AgentJob, job_id)
            if job is None:
                return
            if node_map_version is not None and job.feed_sync_run_id is not None:
                run = await db.get(FeedSyncRun, job.feed_sync_run_id)
                if run is not None:
                    run.node_map_version = node_map_version
            await JOB_LIFECYCLES[JobType.feed_sync].succeed(db, job, outcome)

    async def _fail_feed(self, job_id: uuid.UUID, error: Exception) -> None:
        async with session_scope() as db:
            job = await db.get(AgentJob, job_id)
            if job is None:
                return
            await JOB_LIFECYCLES[JobType.feed_sync].fail(
                db,
                job,
                f"{type(error).__name__}: {error}",
            )

    async def _store_failed_parse_stats(self, run_id: uuid.UUID, parsed: ParseResult) -> None:
        async with session_scope() as db:
            run = await db.get(FeedSyncRun, run_id)
            if run is None:
                return
            run.fetched_lines = parsed.physical_line_count
            run.valid = parsed.valid_distinct_count
            run.duplicates = parsed.duplicate_count
            run.skipped_invalid = parsed.invalid_count
            await db.flush()

    def _log_summary(
        self,
        *,
        source_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        metrics: _RunMetrics,
        duration_ms: int,
        status: FeedSyncStatus,
    ) -> None:
        logger.info(
            "Feed sync summary",
            extra={
                "source_id": str(source_id) if source_id is not None else None,
                "run_id": str(run_id) if run_id is not None else None,
                "fetched_lines": metrics.fetched_lines,
                "valid": metrics.valid,
                "duplicates": metrics.duplicates,
                "skipped_invalid": metrics.skipped_invalid,
                "added": metrics.added,
                "removed": metrics.removed,
                "overlap_count": metrics.overlap_count,
                "global_changed": metrics.global_changed,
                "duration_ms": duration_ms,
                "status": status,
            },
        )


async def _locked_global_state(db: AsyncSession) -> GlobalDenyState | None:
    """Load the singleton row under the same short lock used by reconciliation."""
    return (
        (await db.execute(select(GlobalDenyState).where(GlobalDenyState.id == 1).with_for_update()))
        .scalars()
        .one_or_none()
    )


def _state_needs_apply(state: GlobalDenyState) -> bool:
    return (
        state.desired_revision != state.active_revision
        or state.desired_digest != state.active_digest
    )


def _set_parse_metrics(metrics: _RunMetrics, parsed: ParseResult) -> None:
    metrics.fetched_lines = parsed.physical_line_count
    metrics.valid = parsed.valid_distinct_count
    metrics.duplicates = parsed.duplicate_count
    metrics.skipped_invalid = parsed.invalid_count


def _set_reconcile_metrics(metrics: _RunMetrics, result: ReconcileResult) -> None:
    metrics.valid = result.valid
    metrics.added = result.added
    metrics.removed = result.removed
    metrics.overlap_count = result.overlap_count
    metrics.global_changed = result.global_changed


def _parse_failure_message(parsed: ParseResult) -> str:
    if parsed.invalid_line_diagnostics and parsed.invalid_line_diagnostics[0].line_number is None:
        return "Feed response is not valid UTF-8"
    return "Feed response did not contain a valid IPv4 CIDR"


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
