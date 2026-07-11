from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError("expected an object")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected an integer")
    return value


def _counter_map(value: object) -> dict[str, int]:
    return {key: _integer(counter) for key, counter in _mapping(value).items()}


@dataclass(frozen=True, slots=True)
class ServiceCounters:
    dp_id: int
    clean_pkts: int
    clean_bytes: int
    drop_pkts: int
    drop_bytes: int
    drop_by_reason: dict[str, int]

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ServiceCounters:
        return cls(
            dp_id=_integer(payload["dp_id"]),
            clean_pkts=_integer(payload["clean_pkts"]),
            clean_bytes=_integer(payload["clean_bytes"]),
            drop_pkts=_integer(payload["drop_pkts"]),
            drop_bytes=_integer(payload["drop_bytes"]),
            drop_by_reason=_counter_map(payload["drop_by_reason"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "dp_id": self.dp_id,
            "clean_pkts": self.clean_pkts,
            "clean_bytes": self.clean_bytes,
            "drop_pkts": self.drop_pkts,
            "drop_bytes": self.drop_bytes,
            "drop_by_reason": self.drop_by_reason,
        }


@dataclass(frozen=True, slots=True)
class NodeCounters:
    counters: dict[str, int]
    sample_stats: dict[str, int]
    bloom_stats: dict[str, int]

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> NodeCounters:
        return cls(
            counters=_counter_map(payload["counters"]),
            sample_stats=_counter_map(payload["sample_stats"]),
            bloom_stats=_counter_map(payload["bloom_stats"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "counters": self.counters,
            "sample_stats": self.sample_stats,
            "bloom_stats": self.bloom_stats,
        }


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    ts_ns: int
    active_slot: int
    active_version: int
    xdp_mode: str
    xdp_prog_id: int
    xdp_ifindex: int
    node: NodeCounters
    services: tuple[ServiceCounters, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> TelemetrySnapshot:
        active = _mapping(payload["active"])
        xdp = _mapping(payload["xdp"])
        services = payload["services"]
        if not isinstance(services, list):
            raise ValueError("expected services list")

        xdp_mode = xdp["mode"]
        if not isinstance(xdp_mode, str):
            raise ValueError("expected XDP mode")

        return cls(
            ts_ns=_integer(payload["ts_ns"]),
            active_slot=_integer(active["slot"]),
            active_version=_integer(active["version"]),
            xdp_mode=xdp_mode,
            xdp_prog_id=_integer(xdp["prog_id"]),
            xdp_ifindex=_integer(xdp["ifindex"]),
            node=NodeCounters.from_dict(_mapping(payload["node"])),
            services=tuple(ServiceCounters.from_dict(_mapping(service)) for service in services),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ts_ns": self.ts_ns,
            "active": {"slot": self.active_slot, "version": self.active_version},
            "xdp": {
                "mode": self.xdp_mode,
                "prog_id": self.xdp_prog_id,
                "ifindex": self.xdp_ifindex,
            },
            "node": self.node.to_dict(),
            "services": [service.to_dict() for service in self.services],
        }


class TelemetryReader:
    def __init__(
        self,
        *,
        binary: str,
        ifindex: int | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.binary = binary
        self.ifindex = ifindex
        self.timeout_seconds = timeout_seconds

    async def snapshot(self) -> TelemetrySnapshot | None:
        command = [self.binary, "snapshot", "--json"]
        if self.ifindex is not None:
            command.extend(("--ifindex", str(self.ifindex)))

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            return None

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return None

        if process.returncode != 0:
            return None

        try:
            output = stdout.decode()
            if "not loaded" in output.lower() or "not loaded" in stderr.decode().lower():
                return None
            return TelemetrySnapshot.from_dict(_mapping(json.loads(output)))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


class FakeTelemetryReader:
    def __init__(self, *, snapshots: Iterable[TelemetrySnapshot | None]) -> None:
        self._snapshots = deque(snapshots)

    async def snapshot(self) -> TelemetrySnapshot | None:
        if not self._snapshots:
            return None
        return self._snapshots.popleft()
