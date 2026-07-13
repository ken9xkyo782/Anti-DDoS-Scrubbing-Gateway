import asyncio
import json
import stat
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AgentJob,
    ApplyStatus,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    GlobalDenyState,
    JobStatus,
    JobType,
)
from app.services.feed_reconcile import GlobalDenySnapshot, materialize_global_union
from app.worker.applier import ApplyError, GlobalDenyApplier, PlaceholderApplier
from app.worker.feed_runner import FeedRunner
from app.worker.handlers import configure_feed_runner
from app.worker.processor import process_job

pytestmark = pytest.mark.integration


def create_fake_helper(
    tmp_path: Path,
    *,
    stdout: str = '{"active_slot": 1, "node_map_version": 7}',
    exit_code: int = 0,
    sleep_seconds: float = 0,
) -> tuple[Path, Path, Path, Path]:
    snapshot_record = tmp_path / "snapshot.bin"
    argv_record = tmp_path / "argv.json"
    mode_record = tmp_path / "mode.txt"
    helper = tmp_path / "fake-global-apply"
    helper.write_text(
        f"""#!{sys.executable}
import pathlib
import json
import stat
import sys
import time

snapshot = pathlib.Path(sys.argv[1])
pathlib.Path({str(snapshot_record)!r}).write_bytes(snapshot.read_bytes())
pathlib.Path({str(argv_record)!r}).write_text(json.dumps(sys.argv[1:]))
pathlib.Path({str(mode_record)!r}).write_text(str(stat.S_IMODE(snapshot.stat().st_mode)))
time.sleep({sleep_seconds!r})
print({stdout!r})
sys.exit({exit_code!r})
"""
    )
    helper.chmod(0o755)
    return helper, snapshot_record, argv_record, mode_record


def global_snapshot() -> GlobalDenySnapshot:
    return GlobalDenySnapshot(
        revision=42,
        digest="a" * 64,
        cidrs=("45.45.0.0/16", "192.0.2.0/24", "203.0.113.5/32"),
    )


async def test_global_applier_writes_a_private_v2_snapshot_and_parses_result(
    tmp_path: Path,
) -> None:
    helper, snapshot_record, argv_record, mode_record = create_fake_helper(tmp_path)
    applier = GlobalDenyApplier(apply_bin=str(helper), timeout_seconds=1)

    result = await applier.apply_global(global_snapshot())

    assert (result.active_slot, result.node_map_version) == (1, 7)
    assert snapshot_record.read_bytes()[8:16] == b"\x02\x00\x00\x00\x02\x00\x00\x00"
    assert len(json.loads(argv_record.read_text())) == 1
    assert stat.S_IMODE(int(mode_record.read_text())) == 0o600


@pytest.mark.parametrize(
    ("stdout", "exit_code", "sleep_seconds", "timeout_seconds", "message"),
    [
        ('{"active_slot": 2, "node_map_version": 1}', 0, 0, 1, "malformed"),
        ('{"active_slot": 1}', 0, 0, 1, "malformed"),
        ("ignored", 9, 0, 1, "exit status 9"),
        ('{"active_slot": 1, "node_map_version": 1}', 0, 1, 0.01, "timed out"),
    ],
)
async def test_global_applier_rejects_helper_failures_without_leaking_snapshot(
    tmp_path: Path,
    stdout: str,
    exit_code: int,
    sleep_seconds: float,
    timeout_seconds: float,
    message: str,
) -> None:
    helper, _, argv_record, _ = create_fake_helper(
        tmp_path,
        stdout=stdout,
        exit_code=exit_code,
        sleep_seconds=sleep_seconds,
    )
    applier = GlobalDenyApplier(apply_bin=str(helper), timeout_seconds=timeout_seconds)

    with pytest.raises(ApplyError, match=message):
        await applier.apply_global(global_snapshot())

    if argv_record.exists():
        snapshot_path = Path(json.loads(argv_record.read_text())[0])
        assert not await asyncio.to_thread(snapshot_path.exists)


async def seed_global_job(
    committed_db: async_sessionmaker[AsyncSession],
) -> tuple[int, uuid.UUID]:
    async with committed_db() as db:
        db.add(
            BlacklistEntry(
                scope=BlacklistScope.global_,
                source=BlacklistSource.manual,
                source_cidr="198.51.100.0/24",
            )
        )
        await db.flush()
        materialized = await materialize_global_union(db)
        job = AgentJob(
            target_type="global_deny",
            version=materialized.desired_revision,
            job_type=JobType.global_deny_apply,
            trigger=ChangeTrigger.global_deny_retry,
        )
        db.add(job)
        await db.commit()
        return materialized.desired_revision, job.id


async def process_global_job(
    *,
    job_id: uuid.UUID,
    committed_db: async_sessionmaker[AsyncSession],
    global_applier: GlobalDenyApplier,
) -> None:
    client = httpx.AsyncClient()
    configure_feed_runner(
        FeedRunner(client=client, settings=Settings(), global_applier=global_applier)
    )
    try:
        await process_job(job_id, session_factory=committed_db, applier=PlaceholderApplier())
    finally:
        configure_feed_runner(None)
        await client.aclose()


async def test_global_apply_success_advances_only_global_state(
    committed_db: async_sessionmaker,
    tmp_path: Path,
) -> None:
    revision, job_id = await seed_global_job(committed_db)
    helper, _, _, _ = create_fake_helper(tmp_path)

    await process_global_job(
        job_id=job_id,
        committed_db=committed_db,
        global_applier=GlobalDenyApplier(apply_bin=str(helper), timeout_seconds=1),
    )

    async with committed_db() as db:
        state = await db.get(GlobalDenyState, 1)
        job = await db.get(AgentJob, job_id)
        assert state is not None
        assert job is not None
        assert (state.active_revision, state.last_node_map_version) == (revision, 7)
        assert state.apply_status == ApplyStatus.active
        assert job.status == JobStatus.succeeded


async def test_global_apply_failure_marks_job_and_global_state_without_advancing_active(
    committed_db: async_sessionmaker,
    tmp_path: Path,
) -> None:
    revision, job_id = await seed_global_job(committed_db)
    helper, _, _, _ = create_fake_helper(tmp_path, exit_code=9)

    await process_global_job(
        job_id=job_id,
        committed_db=committed_db,
        global_applier=GlobalDenyApplier(apply_bin=str(helper), timeout_seconds=1),
    )

    async with committed_db() as db:
        state = await db.get(GlobalDenyState, 1)
        job = await db.get(AgentJob, job_id)
        assert state is not None
        assert job is not None
        assert (state.desired_revision, state.active_revision) == (revision, 0)
        assert state.apply_status == ApplyStatus.failed
        assert job.status == JobStatus.failed
