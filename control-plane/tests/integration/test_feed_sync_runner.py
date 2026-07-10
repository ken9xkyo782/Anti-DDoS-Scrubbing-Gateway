import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AgentJob,
    ApplyStatus,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    FeedSyncRun,
    FeedSyncStatus,
    GlobalDenyState,
    JobStatus,
    JobType,
    ThreatFeedSource,
    utc_now,
)
from app.db.session import session_scope
from app.worker.applier import PlaceholderApplier
from app.worker.feed_runner import FeedRunner, GlobalDenyApplyResult
from app.worker.handlers import configure_feed_runner
from app.worker.processor import process_job

pytestmark = pytest.mark.integration


@dataclass
class RecordingGlobalApplier:
    node_map_version: int = 1

    def __post_init__(self) -> None:
        self.revisions: list[int] = []

    async def apply_global(self, snapshot: object) -> GlobalDenyApplyResult:
        self.revisions.append(snapshot.revision)  # type: ignore[attr-defined]
        return GlobalDenyApplyResult(active_slot=1, node_map_version=self.node_map_version)


class FailingGlobalApplier:
    async def apply_global(self, snapshot: object) -> GlobalDenyApplyResult:
        del snapshot
        raise RuntimeError("global apply failed")


async def seed_feed_job(
    *,
    name: str,
    dry_run: bool = False,
    credential_env_var: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with session_scope() as db:
        source = ThreatFeedSource(
            name=name,
            url="https://feeds.example.test/deny.txt",
            credential_env_var=credential_env_var,
            sync_interval_seconds=300,
        )
        db.add(source)
        await db.flush()
        run = FeedSyncRun(
            feed_source_id=source.id,
            source_name=source.name,
            sequence=1,
            trigger=ChangeTrigger.feed_dry_run if dry_run else ChangeTrigger.feed_manual,
            dry_run=dry_run,
        )
        db.add(run)
        await db.flush()
        job = AgentJob(
            target_type="feed_sync_run",
            feed_sync_run_id=run.id,
            version=run.sequence,
            job_type=JobType.feed_sync,
            trigger=run.trigger,
        )
        db.add(job)
        await db.flush()
    return source.id, run.id, job.id


async def seed_global_job(*, revision: int) -> uuid.UUID:
    async with session_scope() as db:
        job = AgentJob(
            target_type="global_deny",
            version=revision,
            job_type=JobType.global_deny_apply,
            trigger=ChangeTrigger.global_deny_retry,
        )
        db.add(job)
        await db.flush()
    return job.id


async def feed_records(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[ThreatFeedSource, FeedSyncRun, AgentJob]:
    async with session_factory() as db:
        source = await db.get(ThreatFeedSource, source_id)
        run = await db.get(FeedSyncRun, run_id)
        job = await db.get(AgentJob, job_id)
        assert source is not None
        assert run is not None
        assert job is not None
        return source, run, job


async def global_state(session_factory: async_sessionmaker[AsyncSession]) -> GlobalDenyState | None:
    async with session_factory() as db:
        return await db.get(GlobalDenyState, 1)


async def feed_cidrs(session_factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(BlacklistEntry.source_cidr)
            .where(
                BlacklistEntry.scope == BlacklistScope.global_,
                BlacklistEntry.source == BlacklistSource.feed,
            )
            .order_by(BlacklistEntry.source_cidr)
        )
        return [str(cidr) for cidr in rows]


def runner_for(
    handler: Callable[[httpx.Request], httpx.Response] | bytes,
    global_applier: RecordingGlobalApplier | FailingGlobalApplier,
) -> tuple[FeedRunner, httpx.AsyncClient]:
    if isinstance(handler, bytes):
        body = handler
        handler = _response_with(body)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FeedRunner(client=client, settings=Settings(), global_applier=global_applier), client


def _response_with(body: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=body)

    return handler


async def process_with_runner(
    *,
    job_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    runner: FeedRunner,
) -> None:
    configure_feed_runner(runner)
    try:
        await process_job(job_id, session_factory=session_factory, applier=PlaceholderApplier())
    finally:
        configure_feed_runner(None)


async def test_sync_success_reconciles_and_converges_global_deny(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-success")
    applier = RecordingGlobalApplier(node_map_version=11)
    runner, client = runner_for(b"198.51.100.10\n198.51.100.11\n", applier)

    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    source, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    state = await global_state(committed_db)
    assert job.error is None
    assert await feed_cidrs(committed_db) == ["198.51.100.10/32", "198.51.100.11/32"]
    assert job.status == JobStatus.succeeded
    assert run.status == FeedSyncStatus.success
    assert (run.fetched_lines, run.valid, run.duplicates, run.added, run.removed) == (2, 2, 0, 2, 0)
    assert run.node_map_version == 11
    assert source.last_status == FeedSyncStatus.success
    assert applier.revisions == [1]
    assert state is not None
    assert (state.desired_revision, state.active_revision) == (1, 1)
    assert state.apply_status == ApplyStatus.active


async def test_sync_partial_applies_the_valid_subset(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-partial")
    applier = RecordingGlobalApplier()
    runner, client = runner_for(b"198.51.100.20\nnot-an-ip\n198.51.100.21\n", applier)

    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    assert job.status == JobStatus.succeeded
    assert run.status == FeedSyncStatus.partial
    assert (run.valid, run.skipped_invalid, run.added) == (2, 1, 2)
    assert await feed_cidrs(committed_db) == ["198.51.100.20/32", "198.51.100.21/32"]
    assert applier.revisions == [1]


@pytest.mark.parametrize(
    ("handler", "case"),
    [
        (lambda request: httpx.Response(500), "http"),
        (b"\xff\xfe", "encoding"),
        (b"# intentionally empty\nnot-an-ip\n", "zero-valid"),
    ],
)
async def test_keep_last_for_fetch_encoding_and_zero_valid_failures(
    committed_db: async_sessionmaker[AsyncSession],
    handler: Callable[[httpx.Request], httpx.Response] | bytes,
    case: str,
) -> None:
    source_id, first_run_id, first_job_id = await seed_feed_job(name=f"runner-keep-{case}")
    applier = RecordingGlobalApplier()
    first_runner, first_client = runner_for(b"198.51.100.30\n", applier)
    try:
        await process_with_runner(
            job_id=first_job_id, session_factory=committed_db, runner=first_runner
        )
    finally:
        await first_client.aclose()

    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        run = FeedSyncRun(
            feed_source_id=source.id,
            source_name=source.name,
            sequence=2,
            trigger=ChangeTrigger.feed_manual,
        )
        job = AgentJob(
            target_type="feed_sync_run",
            feed_sync_run=run,
            version=run.sequence,
            job_type=JobType.feed_sync,
            trigger=run.trigger,
        )
        db.add_all([run, job])
        await db.flush()
        second_run_id, second_job_id = run.id, job.id

    failing_runner, failing_client = runner_for(handler, applier)
    try:
        await process_with_runner(
            job_id=second_job_id, session_factory=committed_db, runner=failing_runner
        )
    finally:
        await failing_client.aclose()

    _, first_run, _ = await feed_records(
        committed_db,
        source_id=source_id,
        run_id=first_run_id,
        job_id=first_job_id,
    )
    source, second_run, second_job = await feed_records(
        committed_db,
        source_id=source_id,
        run_id=second_run_id,
        job_id=second_job_id,
    )
    state = await global_state(committed_db)
    assert first_run.status == FeedSyncStatus.success
    assert await feed_cidrs(committed_db) == ["198.51.100.30/32"]
    assert second_job.status == JobStatus.failed
    assert second_run.status == FeedSyncStatus.failed
    assert source.last_status == FeedSyncStatus.failed
    assert state is not None
    assert (state.desired_revision, state.active_revision) == (1, 1)
    assert applier.revisions == [1]


async def test_dry_run_persists_stats_without_blacklist_or_global_state_mutation(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-dry-run", dry_run=True)
    applier = RecordingGlobalApplier()
    runner, client = runner_for(b"198.51.100.40\ninvalid\n", applier)

    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    assert job.status == JobStatus.succeeded
    assert run.status == FeedSyncStatus.partial
    assert (run.fetched_lines, run.valid, run.skipped_invalid, run.added, run.removed) == (
        2,
        1,
        1,
        1,
        0,
    )
    assert run.desired_revision is None
    assert await feed_cidrs(committed_db) == []
    assert await global_state(committed_db) is None
    assert applier.revisions == []


async def test_byte_identical_replay_records_zero_delta_and_skips_converged_apply(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, first_run_id, first_job_id = await seed_feed_job(name="runner-noop")
    applier = RecordingGlobalApplier()
    runner, client = runner_for(b"198.51.100.50\n", applier)
    try:
        await process_with_runner(job_id=first_job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        run = FeedSyncRun(
            feed_source_id=source.id,
            source_name=source.name,
            sequence=2,
            trigger=ChangeTrigger.feed_manual,
        )
        job = AgentJob(
            target_type="feed_sync_run",
            feed_sync_run=run,
            version=run.sequence,
            job_type=JobType.feed_sync,
            trigger=run.trigger,
        )
        db.add_all([run, job])
        await db.flush()
        second_run_id, second_job_id = run.id, job.id

    runner, client = runner_for(b"198.51.100.50\n", applier)
    try:
        await process_with_runner(job_id=second_job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, first_run, _ = await feed_records(
        committed_db,
        source_id=source_id,
        run_id=first_run_id,
        job_id=first_job_id,
    )
    _, second_run, second_job = await feed_records(
        committed_db,
        source_id=source_id,
        run_id=second_run_id,
        job_id=second_job_id,
    )
    assert first_run.status == FeedSyncStatus.success
    assert second_job.status == JobStatus.succeeded
    assert (second_run.added, second_run.removed, second_run.global_changed) == (0, 0, False)
    assert second_run.node_map_version is None
    assert applier.revisions == [1]


async def test_duplicate_delivery_is_a_terminal_no_op(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-duplicate")
    applier = RecordingGlobalApplier()
    runner, client = runner_for(b"198.51.100.60\n", applier)
    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    assert (job.status, job.attempts, run.status) == (
        JobStatus.succeeded,
        1,
        FeedSyncStatus.success,
    )
    assert applier.revisions == [1]


async def test_applier_failure_preserves_desired_active_divergence_for_retry(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-apply-failure")
    runner, client = runner_for(b"198.51.100.70\n", FailingGlobalApplier())
    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    state = await global_state(committed_db)
    assert job.status == JobStatus.failed
    assert run.status == FeedSyncStatus.failed
    assert await feed_cidrs(committed_db) == ["198.51.100.70/32"]
    assert state is not None
    assert (state.desired_revision, state.active_revision) == (1, 0)


async def test_global_convergence_retry_uses_desired_revision_without_fetching(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, feed_job_id = await seed_feed_job(name="runner-converge")
    failing_runner, failing_client = runner_for(b"198.51.100.80\n", FailingGlobalApplier())
    try:
        await process_with_runner(
            job_id=feed_job_id, session_factory=committed_db, runner=failing_runner
        )
    finally:
        await failing_client.aclose()

    retry_job_id = await seed_global_job(revision=1)
    applier = RecordingGlobalApplier(node_map_version=22)
    no_fetch_runner, client = runner_for(
        lambda request: (_ for _ in ()).throw(AssertionError("convergence must not fetch")), applier
    )
    try:
        await process_with_runner(
            job_id=retry_job_id, session_factory=committed_db, runner=no_fetch_runner
        )
        await process_with_runner(
            job_id=retry_job_id, session_factory=committed_db, runner=no_fetch_runner
        )
    finally:
        await client.aclose()

    _, run, feed_job = await feed_records(
        committed_db,
        source_id=source_id,
        run_id=run_id,
        job_id=feed_job_id,
    )
    state = await global_state(committed_db)
    async with committed_db() as db:
        retry_job = await db.get(AgentJob, retry_job_id)
        assert retry_job is not None
    assert run.status == FeedSyncStatus.failed
    assert feed_job.status == JobStatus.failed
    assert retry_job.status == JobStatus.succeeded
    assert retry_job.attempts == 1
    assert applier.revisions == [1]
    assert state is not None
    assert (state.desired_revision, state.active_revision, state.last_node_map_version) == (
        1,
        1,
        22,
    )


async def test_stale_global_convergence_job_dedupes_by_desired_revision(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    _, _, feed_job_id = await seed_feed_job(name="runner-stale-converge")
    failing_runner, failing_client = runner_for(b"198.51.100.81\n", FailingGlobalApplier())
    try:
        await process_with_runner(
            job_id=feed_job_id,
            session_factory=committed_db,
            runner=failing_runner,
        )
    finally:
        await failing_client.aclose()

    stale_job_id = await seed_global_job(revision=0)
    applier = RecordingGlobalApplier()
    runner, client = runner_for(
        lambda request: (_ for _ in ()).throw(AssertionError("convergence must not fetch")), applier
    )
    try:
        await process_with_runner(job_id=stale_job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    state = await global_state(committed_db)
    async with committed_db() as db:
        stale_job = await db.get(AgentJob, stale_job_id)
        assert stale_job is not None
    assert stale_job.status == JobStatus.succeeded
    assert applier.revisions == []
    assert state is not None
    assert (state.desired_revision, state.active_revision) == (1, 0)


async def test_deleted_source_after_claim_noops_without_fetch(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-deleted")
    async with session_scope() as db:
        source = await db.get(ThreatFeedSource, source_id)
        assert source is not None
        source.deleted_at = utc_now()

    applier = RecordingGlobalApplier()
    runner, client = runner_for(
        lambda request: (_ for _ in ()).throw(AssertionError("deleted source must not fetch")),
        applier,
    )
    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    assert (job.status, run.status) == (JobStatus.succeeded, FeedSyncStatus.success)
    assert applier.revisions == []


async def test_source_deleted_during_fetch_finishes_as_a_safe_noop(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    source_id, run_id, job_id = await seed_feed_job(name="runner-deleted-in-flight")

    async def delete_source_during_fetch(request: httpx.Request) -> httpx.Response:
        del request
        async with session_scope() as db:
            source = await db.get(ThreatFeedSource, source_id)
            assert source is not None
            source.deleted_at = utc_now()
        return httpx.Response(200, content=b"198.51.100.85\n")

    applier = RecordingGlobalApplier()
    client = httpx.AsyncClient(transport=httpx.MockTransport(delete_source_during_fetch))
    runner = FeedRunner(client=client, settings=Settings(), global_applier=applier)
    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    _, run, job = await feed_records(
        committed_db, source_id=source_id, run_id=run_id, job_id=job_id
    )
    assert (job.status, run.status) == (JobStatus.succeeded, FeedSyncStatus.success)
    assert await feed_cidrs(committed_db) == []
    assert applier.revisions == []


async def test_failed_source_does_not_change_another_source_assertions(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    first_source_id, first_run_id, first_job_id = await seed_feed_job(name="runner-isolation-first")
    second_source_id, second_run_id, second_job_id = await seed_feed_job(
        name="runner-isolation-second"
    )
    applier = RecordingGlobalApplier()
    success_runner, success_client = runner_for(b"198.51.100.90\n", applier)
    try:
        await process_with_runner(
            job_id=first_job_id, session_factory=committed_db, runner=success_runner
        )
    finally:
        await success_client.aclose()

    failed_runner, failed_client = runner_for(lambda request: httpx.Response(500), applier)
    try:
        await process_with_runner(
            job_id=second_job_id, session_factory=committed_db, runner=failed_runner
        )
    finally:
        await failed_client.aclose()

    _, first_run, first_job = await feed_records(
        committed_db,
        source_id=first_source_id,
        run_id=first_run_id,
        job_id=first_job_id,
    )
    _, second_run, second_job = await feed_records(
        committed_db,
        source_id=second_source_id,
        run_id=second_run_id,
        job_id=second_job_id,
    )
    assert (first_job.status, first_run.status) == (JobStatus.succeeded, FeedSyncStatus.success)
    assert (second_job.status, second_run.status) == (JobStatus.failed, FeedSyncStatus.failed)
    assert await feed_cidrs(committed_db) == ["198.51.100.90/32"]


async def test_summary_log_is_structured_and_excludes_feed_secrets(
    committed_db: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_name = "RUNNER_FEED_TOKEN"
    secret = "not-for-feed-logs"
    monkeypatch.setenv(env_name, secret)
    source_id, run_id, job_id = await seed_feed_job(
        name="runner-observability",
        credential_env_var=env_name,
    )
    applier = RecordingGlobalApplier()
    runner, client = runner_for(b"198.51.100.100 # private body\n", applier)
    monkeypatch.setattr(logging.getLogger("app.worker.feed_runner"), "disabled", False)
    caplog.set_level(logging.INFO, logger="app.worker.feed_runner")

    try:
        await process_with_runner(job_id=job_id, session_factory=committed_db, runner=runner)
    finally:
        await client.aclose()

    summaries = [record for record in caplog.records if record.msg == "Feed sync summary"]
    assert len(summaries) == 1
    record = summaries[0]
    assert record.source_id == str(source_id)
    assert record.run_id == str(run_id)
    assert record.status == FeedSyncStatus.success
    assert record.valid == 1
    assert record.duration_ms >= 0
    assert secret not in caplog.text
    assert env_name not in caplog.text
    assert "https://feeds.example.test/deny.txt" not in caplog.text
    assert "private body" not in caplog.text
    assert "Bearer" not in caplog.text
    assert all(secret not in str(record.__dict__) for record in summaries)
    assert os.environ[env_name] == secret
