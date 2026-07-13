import asyncio
import stat
import struct
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AgentJob,
    AllowRule,
    ApplyStatus,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ChangeTrigger,
    JobStatus,
    ProtectedService,
    Protocol,
    ServicePlan,
    Tenant,
    WhitelistEntry,
)
from app.db.session import session_scope
from app.services.apply import enqueue_service_update
from app.services.services import bump_version
from app.worker.applier import DoubleBufferApplier
from app.worker.processor import process_job

pytestmark = pytest.mark.integration


def create_fake_helper(
    tmp_path: Path,
    *,
    exit_code: int = 0,
    sleep_seconds: float = 0,
    wait_for_release: bool = False,
) -> tuple[Path, Path, Path, Path, Path]:
    snapshot_record = tmp_path / "snapshot.bin"
    argv_record = tmp_path / "argv.txt"
    mode_record = tmp_path / "mode.txt"
    started = tmp_path / "started"
    release = tmp_path / "release"
    helper = tmp_path / "fake-xdpgw-apply"
    helper.write_text(
        f"""#!{sys.executable}
import pathlib
import stat
import sys
import time

snapshot = pathlib.Path(sys.argv[1])
pathlib.Path({str(snapshot_record)!r}).write_bytes(snapshot.read_bytes())
pathlib.Path({str(argv_record)!r}).write_text("\\n".join(sys.argv[1:]))
pathlib.Path({str(mode_record)!r}).write_text(str(stat.S_IMODE(snapshot.stat().st_mode)))
pathlib.Path({str(started)!r}).touch()
if {wait_for_release!r}:
    while not pathlib.Path({str(release)!r}).exists():
        time.sleep(0.01)
time.sleep({sleep_seconds!r})
if {exit_code!r}:
    print("fake helper failure", file=sys.stderr)
sys.exit({exit_code!r})
"""
    )
    helper.chmod(0o755)
    return helper, snapshot_record, argv_record, mode_record, release


async def create_apply_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    cidr_or_ip: str,
    enabled: bool = True,
) -> tuple[ProtectedService, AgentJob]:
    async with session_factory() as db:
        tenant = Tenant(name=f"{name}-tenant")
        service = ProtectedService(
            tenant=tenant,
            name=name,
            cidr_or_ip=cidr_or_ip,
            enabled=enabled,
            apply_status=ApplyStatus.pending,
            version=1,
            vip_pps=1_000,
            vip_bps=8_000_000,
        )
        plan = ServicePlan(
            service=service,
            committed_clean_gbps=Decimal("1"),
            ceiling_clean_gbps=Decimal("2"),
        )
        db.add_all(
            [
                tenant,
                service,
                plan,
                AllowRule(
                    service=service,
                    priority=1,
                    protocol=Protocol.tcp,
                    src_port_lo=443,
                    src_port_hi=443,
                    dst_port_lo=443,
                    dst_port_hi=443,
                ),
                WhitelistEntry(service=service, source_cidr="198.51.100.0/24"),
                BlacklistEntry(
                    service=service,
                    scope=BlacklistScope.service,
                    source=BlacklistSource.manual,
                    source_cidr="203.0.113.12/32",
                ),
            ]
        )
        await db.flush()
        job = await enqueue_service_update(db, service, actor=None, trigger=ChangeTrigger.service)
        await db.commit()
        return service, job


async def get_service_and_job(
    session_factory: async_sessionmaker[AsyncSession],
    service_id: object,
    job_id: object,
) -> tuple[ProtectedService, AgentJob]:
    async with session_factory() as db:
        service = await db.get(ProtectedService, service_id)
        job = await db.get(AgentJob, job_id)
        assert service is not None
        assert job is not None
        return service, job


async def wait_for_path(path: Path) -> None:
    for _ in range(100):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


async def test_double_buffer_applier_builds_full_node_snapshot_and_marks_active(
    committed_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    service, job = await create_apply_job(
        committed_db,
        name="double-buffer-success",
        cidr_or_ip="203.0.113.70/32",
    )
    async with committed_db() as db:
        sibling = ProtectedService(
            tenant_id=service.tenant_id,
            name="double-buffer-sibling",
            cidr_or_ip="203.0.113.71/32",
            enabled=True,
        )
        disabled = ProtectedService(
            tenant_id=service.tenant_id,
            name="double-buffer-disabled",
            cidr_or_ip="203.0.113.72/32",
            enabled=False,
        )
        db.add_all([sibling, disabled])
        await db.commit()

    helper, snapshot_record, argv_record, mode_record, _ = create_fake_helper(tmp_path)
    applier = DoubleBufferApplier(
        session_factory=committed_db,
        apply_bin=str(helper),
        timeout_seconds=1,
    )

    await process_job(job.id, session_factory=committed_db, applier=applier)

    updated_service, updated_job = await get_service_and_job(committed_db, service.id, job.id)
    snapshot_path = Path(argv_record.read_text().splitlines()[0])
    assert updated_service.apply_status == ApplyStatus.active
    assert updated_service.active_version == 1
    assert updated_job.status == JobStatus.succeeded
    assert snapshot_record.read_bytes()[:8] == b"XDPGWAP1"
    assert struct.unpack_from("<I", snapshot_record.read_bytes(), 12)[0] == 1
    assert struct.unpack_from("<I", snapshot_record.read_bytes(), 16)[0] == 2
    assert stat.S_IMODE(int(mode_record.read_text())) == 0o600
    assert not await asyncio.to_thread(snapshot_path.exists)


async def test_double_buffer_applier_nonzero_helper_marks_failed_without_advancing_version(
    committed_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    service, job = await create_apply_job(
        committed_db,
        name="double-buffer-failure",
        cidr_or_ip="203.0.113.73/32",
    )
    helper, _, _, _, _ = create_fake_helper(tmp_path, exit_code=9)

    await process_job(
        job.id,
        session_factory=committed_db,
        applier=DoubleBufferApplier(
            session_factory=committed_db,
            apply_bin=str(helper),
            timeout_seconds=1,
        ),
    )

    updated_service, updated_job = await get_service_and_job(committed_db, service.id, job.id)
    assert updated_service.apply_status == ApplyStatus.failed
    assert updated_service.active_version is None
    assert updated_job.status == JobStatus.failed
    assert updated_job.error is not None
    assert "fake helper failure" in updated_job.error


async def test_double_buffer_applier_timeout_marks_failed_without_advancing_version(
    committed_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    service, job = await create_apply_job(
        committed_db,
        name="double-buffer-timeout",
        cidr_or_ip="203.0.113.74/32",
    )
    helper, _, _, _, _ = create_fake_helper(tmp_path, sleep_seconds=1)

    await process_job(
        job.id,
        session_factory=committed_db,
        applier=DoubleBufferApplier(
            session_factory=committed_db,
            apply_bin=str(helper),
            timeout_seconds=0.01,
        ),
    )

    updated_service, updated_job = await get_service_and_job(committed_db, service.id, job.id)
    assert updated_service.apply_status == ApplyStatus.failed
    assert updated_service.active_version is None
    assert updated_job.status == JobStatus.failed
    assert updated_job.error is not None
    assert "timed out" in updated_job.error


async def test_double_buffer_applier_supersedes_mid_apply_without_second_advance(
    committed_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    service, first_job = await create_apply_job(
        committed_db,
        name="double-buffer-superseded",
        cidr_or_ip="203.0.113.75/32",
    )
    helper, _, _, _, release = create_fake_helper(tmp_path, wait_for_release=True)
    applier = DoubleBufferApplier(
        session_factory=committed_db,
        apply_bin=str(helper),
        timeout_seconds=1,
    )
    first = asyncio.create_task(
        process_job(first_job.id, session_factory=committed_db, applier=applier)
    )

    await wait_for_path(tmp_path / "started")
    async with session_scope() as db:
        current = await db.get(ProtectedService, service.id)
        assert current is not None
        await bump_version(db, service.id)
        second_job = await enqueue_service_update(
            db,
            current,
            actor=None,
            trigger=ChangeTrigger.rule,
        )
    release.touch()
    await asyncio.wait_for(first, timeout=2)
    await process_job(second_job.id, session_factory=committed_db, applier=applier)

    updated_service, updated_first_job = await get_service_and_job(
        committed_db,
        service.id,
        first_job.id,
    )
    _, updated_second_job = await get_service_and_job(committed_db, service.id, second_job.id)
    assert updated_service.active_version == 2
    assert updated_first_job.status == JobStatus.superseded
    assert updated_second_job.status == JobStatus.succeeded
