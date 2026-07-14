import asyncio
import json
from pathlib import Path

import pytest

from app.worker.telemetry_reader import (
    DropEvent,
    FakeTelemetryReader,
    TelemetryReader,
    TelemetrySnapshot,
)

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


class StreamingProcess:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdout = asyncio.StreamReader()
        for line in lines:
            self.stdout.feed_data(line)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode: int | None = 0
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = 0


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
    assert expected.bypass_active is False
    assert expected.bypass_pkts == 0
    assert expected.bypass_bytes == 0
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
def test_snapshot_parses_and_round_trips_bypass_state(
    golden_payload: dict[str, object],
) -> None:
    payload = {
        **golden_payload,
        "node_control": {"bypass": 1},
        "bypass": {"pkts": 42, "bytes": 4_200},
    }

    snapshot = TelemetrySnapshot.from_dict(payload)

    assert snapshot.bypass_active is True
    assert snapshot.bypass_pkts == 42
    assert snapshot.bypass_bytes == 4_200
    assert snapshot.to_dict() == payload


@pytest.mark.unit
async def test_fake_telemetry_reader_returns_canned_snapshots(
    golden_payload: dict[str, object],
) -> None:
    snapshot = TelemetrySnapshot.from_dict(
        {
            **golden_payload,
            "node_control": {"bypass": 1},
            "bypass": {"pkts": 42, "bytes": 4_200},
        }
    )
    reader = FakeTelemetryReader(snapshots=[snapshot, None])

    returned_snapshot = await reader.snapshot()
    assert returned_snapshot is snapshot
    assert returned_snapshot.bypass_active is True
    assert returned_snapshot.bypass_pkts == 42
    assert returned_snapshot.bypass_bytes == 4_200
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


@pytest.mark.unit
async def test_tail_parses_sampled_json_lines_from_one_streaming_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "ts_ns": 1_000_000_000,
        "reason": "rate_limit_drop",
        "src_ip": "198.51.100.10",
        "dst_ip": "203.0.113.10",
        "sport": 1234,
        "dport": 443,
        "ip_proto": 6,
        "service_id": 7,
    }
    process = StreamingProcess([json.dumps(payload).encode() + b"\n"])
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def create_process(*args: str, **kwargs: object) -> StreamingProcess:
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    reader = TelemetryReader(binary="/opt/dpstat")
    events = [event async for event in reader.tail()]

    assert events == [
        DropEvent(
            ts_ns=1_000_000_000,
            reason="rate_limit_drop",
            src_ip="198.51.100.10",
            dst_ip="203.0.113.10",
            sport=1234,
            dport=443,
            ip_proto=6,
            service_id=7,
        )
    ]
    assert calls == [
        (
            ("/opt/dpstat", "tail", "--json"),
            {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            },
        )
    ]
