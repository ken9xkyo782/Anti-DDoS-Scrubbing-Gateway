import asyncio
import json
from pathlib import Path

import pytest

from app.worker.telemetry_reader import FakeTelemetryReader, TelemetryReader, TelemetrySnapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_FIXTURE = REPOSITORY_ROOT / "data-plane/tests/fixtures/telemetry_snapshot_golden.json"


class CompletedProcess:
    def __init__(self, *, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr


class HangingProcess:
    def __init__(self) -> None:
        self._stopped = asyncio.Event()
        self.killed = False
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._stopped.wait()
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self._stopped.set()


@pytest.fixture
def golden_payload() -> dict[str, object]:
    return json.loads(GOLDEN_FIXTURE.read_text())


@pytest.mark.unit
async def test_snapshot_parses_and_round_trips_golden_fixture(
    monkeypatch: pytest.MonkeyPatch, golden_payload: dict[str, object]
) -> None:
    process = CompletedProcess(stdout=json.dumps(golden_payload).encode())
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def create_process(*args: str, **kwargs: object) -> CompletedProcess:
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    expected = TelemetrySnapshot.from_dict(golden_payload)
    assert expected.to_dict() == golden_payload

    reader = TelemetryReader(binary="/opt/dpstat", ifindex=7, timeout_seconds=0.1)

    assert await reader.snapshot() == expected
    assert calls == [
        (
            ("/opt/dpstat", "snapshot", "--json", "--ifindex", "7"),
            {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            },
        )
    ]


@pytest.mark.unit
async def test_fake_telemetry_reader_returns_canned_snapshots(
    golden_payload: dict[str, object],
) -> None:
    snapshot = TelemetrySnapshot.from_dict(golden_payload)
    reader = FakeTelemetryReader(snapshots=[snapshot, None])

    assert await reader.snapshot() is snapshot
    assert await reader.snapshot() is None
    assert await reader.snapshot() is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [
        (b"{malformed", b"", 0),
        (b"gateway not loaded", b"", 0),
        (b"{}", b"gateway not loaded", 1),
    ],
)
async def test_snapshot_returns_none_for_invalid_or_offline_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
) -> None:
    async def create_process(*args: str, **kwargs: object) -> CompletedProcess:
        return CompletedProcess(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    reader = TelemetryReader(binary="/opt/dpstat", timeout_seconds=0.1)

    assert await reader.snapshot() is None


@pytest.mark.unit
async def test_snapshot_returns_none_and_kills_process_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = HangingProcess()

    async def create_process(*args: str, **kwargs: object) -> HangingProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    reader = TelemetryReader(binary="/opt/dpstat", timeout_seconds=0.01)

    assert await reader.snapshot() is None
    assert process.killed
