import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.worker.telemetry import TelemetryAggregator
from app.worker.telemetry_reader import (
    DropEvent,
    FakeTelemetryReader,
    NodeCounters,
    ServiceCounters,
    TelemetrySnapshot,
)


def snapshot(
    *,
    ts_ns: int,
    version: int,
    clean_pkts: int,
    clean_bytes: int,
    drop_pkts: int,
    drop_bytes: int,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        ts_ns=ts_ns,
        active_slot=0,
        active_version=version,
        xdp_mode="native",
        xdp_prog_id=1,
        xdp_ifindex=2,
        node=NodeCounters(counters={"map_error": 0}, sample_stats={}, bloom_stats={}),
        services=(
            ServiceCounters(
                dp_id=1,
                clean_pkts=clean_pkts,
                clean_bytes=clean_bytes,
                drop_pkts=drop_pkts,
                drop_bytes=drop_bytes,
                drop_by_reason={"rate_limit_drop": drop_pkts},
            ),
        ),
    )


@pytest.mark.unit
def test_counter_delta_uses_raw_value_for_resets_and_version_changes() -> None:
    assert TelemetryAggregator.counter_delta(15, 10, reset=False) == 5
    assert TelemetryAggregator.counter_delta(3, 10, reset=False) == 3
    assert TelemetryAggregator.counter_delta(15, 10, reset=True) == 15


@pytest.mark.unit
async def test_run_loop_logs_and_continues_after_an_aggregation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = FakeTelemetryReader(snapshots=[])
    aggregator = TelemetryAggregator(
        reader=reader,
        session_factory=None,
        interval_seconds=1,
        retention_seconds=60,
        node_clean_capacity_gbps=Decimal("40"),
    )
    stop = asyncio.Event()
    calls = 0

    async def aggregate_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("expected test failure")
        stop.set()

    monkeypatch.setattr(aggregator, "aggregate_once", aggregate_once)

    await aggregator.run_loop(stop)

    assert calls == 2


@pytest.mark.unit
async def test_database_failure_does_not_advance_the_telemetry_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = snapshot(
        ts_ns=1_000_000_000,
        version=1,
        clean_pkts=10,
        clean_bytes=1_000,
        drop_pkts=0,
        drop_bytes=0,
    )
    aggregator = TelemetryAggregator(
        reader=FakeTelemetryReader(snapshots=[first]),
        session_factory=None,
        interval_seconds=1,
        retention_seconds=60,
        node_clean_capacity_gbps=Decimal("40"),
    )

    async def unavailable(*_args: object) -> None:
        raise OperationalError("INSERT", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(aggregator, "_persist_snapshot", unavailable)

    await aggregator.aggregate_once()

    assert aggregator._previous is None


@pytest.mark.unit
async def test_sampled_event_collection_prunes_the_rolling_window_without_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_time = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    reader = FakeTelemetryReader(
        snapshots=[],
        drop_events=[
            DropEvent(1, "rate_limit_drop", "198.51.100.10", "203.0.113.20", 1, 443, 6, 1),
            DropEvent(2, "rate_limit_drop", "198.51.100.11", "203.0.113.20", 2, 53, 17, 1),
        ],
    )
    aggregator = TelemetryAggregator(
        reader=reader,
        session_factory=None,
        interval_seconds=1,
        retention_seconds=60,
        node_clean_capacity_gbps=Decimal("40"),
        top_talkers_window_seconds=1,
    )
    captured_times = iter((initial_time, initial_time + timedelta(seconds=2)))
    monkeypatch.setattr("app.worker.telemetry.utc_now", lambda: next(captured_times))

    await aggregator.collect_sampled_events()

    assert [sample.event.src_ip for sample in aggregator._sampled_events] == ["198.51.100.11"]
