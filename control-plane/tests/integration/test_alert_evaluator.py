import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Alert, AlertNotification, AlertRule, AlertState, AuditEvent
from app.services.alert_rules import AlertInputs, NodeAlertInputs
from app.worker.alert_evaluator import AlertEvaluator

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def clear_alerting_rows(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    async with committed_db() as db:
        await db.execute(delete(AlertNotification))
        await db.execute(delete(Alert))
        await db.execute(delete(AlertRule))
        await db.commit()
    yield
    async with committed_db() as db:
        await db.execute(delete(AlertNotification))
        await db.execute(delete(Alert))
        await db.execute(delete(AlertRule))
        await db.commit()


class RecordingDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, str]] = []
        self.dispatches = 0

    async def enqueue(self, _db: AsyncSession, alert: Alert, trigger: str) -> None:
        self.enqueued.append((alert.id, trigger))

    async def dispatch_pending(self, _db: AsyncSession) -> None:
        self.dispatches += 1


class StaticSources:
    def __init__(self, inputs: AlertInputs) -> None:
        self.inputs = inputs

    async def load(self, _db: AsyncSession, _now: datetime) -> AlertInputs:
        return self.inputs


def node_inputs(
    *,
    map_errors: int | None = None,
    clean_bps: int | None = None,
    capacity_bps: int | None = None,
    maintenance: bool | None = None,
) -> AlertInputs:
    return AlertInputs(
        node=NodeAlertInputs(
            map_error_count=map_errors,
            node_clean_bps=clean_bps,
            node_capacity_bps=capacity_bps,
            maintenance_enabled=maintenance,
        )
    )


def evaluator(
    session_factory: async_sessionmaker[AsyncSession],
    sources: StaticSources,
    dispatcher: RecordingDispatcher,
    now: datetime,
    *,
    fire_ticks: int = 2,
    clear_ticks: int = 2,
) -> AlertEvaluator:
    return AlertEvaluator(
        sources=sources,
        dispatcher=dispatcher,
        session_factory=session_factory,
        fire_ticks=fire_ticks,
        clear_ticks=clear_ticks,
        renotify_seconds=1_800,
        interval_seconds=0.001,
        clock=lambda: now,
    )


async def active_alerts(session_factory: async_sessionmaker[AsyncSession]) -> list[Alert]:
    async with session_factory() as db:
        return list((await db.scalars(select(Alert).order_by(Alert.first_observed_at))).all())


async def test_transient_under_fire_duration_stays_pending_and_silent(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(map_errors=1))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now, fire_ticks=3)

    await lane.tick()
    sources.inputs = node_inputs(map_errors=0)
    await lane.tick()

    alerts = await active_alerts(committed_db)
    assert len(alerts) == 1
    assert alerts[0].state is AlertState.pending
    assert alerts[0].fire_streak == 0
    assert dispatcher.enqueued == []


async def test_fire_deduplicates_and_restart_does_not_refire(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(map_errors=1))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now)

    await lane.tick()
    await lane.tick()
    restarted = evaluator(committed_db, sources, dispatcher, now + timedelta(seconds=10))
    await restarted.tick()

    alerts = await active_alerts(committed_db)
    assert len(alerts) == 1
    assert alerts[0].state is AlertState.firing
    assert alerts[0].fire_streak == 2
    assert [trigger for _, trigger in dispatcher.enqueued] == ["fire"]


async def test_hysteresis_band_holds_then_clear_duration_resolves(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(clean_bps=95, capacity_bps=100))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now)

    await lane.tick()
    await lane.tick()
    sources.inputs = node_inputs(clean_bps=87, capacity_bps=100)
    await lane.tick()
    sources.inputs = node_inputs(clean_bps=84, capacity_bps=100)
    await lane.tick()
    await lane.tick()

    alert = (await active_alerts(committed_db))[0]
    assert alert.state is AlertState.resolved
    assert alert.clear_streak == 2
    assert [trigger for _, trigger in dispatcher.enqueued] == ["fire", "resolve"]


async def test_disabled_rule_auto_resolves_existing_alert(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(map_errors=1))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now)
    await lane.tick()
    await lane.tick()
    async with committed_db() as db:
        db.add(AlertRule(key="map_error", enabled=False))
        await db.commit()

    await lane.tick()

    alert = (await active_alerts(committed_db))[0]
    assert alert.state is AlertState.resolved
    assert [trigger for _, trigger in dispatcher.enqueued] == ["fire", "resolve"]


async def test_absent_source_auto_resolves_existing_alert(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(map_errors=1))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now)
    await lane.tick()
    await lane.tick()
    sources.inputs = node_inputs(map_errors=None)

    await lane.tick()

    alert = (await active_alerts(committed_db))[0]
    assert alert.state is AlertState.resolved
    assert [trigger for _, trigger in dispatcher.enqueued] == ["fire", "resolve"]


async def test_renotify_is_due_only_when_unacknowledged(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(map_errors=1))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now, fire_ticks=1)
    await lane.tick()

    await evaluator(
        committed_db,
        sources,
        dispatcher,
        now + timedelta(seconds=1_801),
        fire_ticks=1,
    ).tick()
    async with committed_db() as db:
        alert = await db.scalar(select(Alert))
        assert alert is not None
        alert.acknowledged_at = now + timedelta(seconds=1_802)
        await db.commit()

    await evaluator(
        committed_db,
        sources,
        dispatcher,
        now + timedelta(seconds=3_700),
        fire_ticks=1,
    ).tick()

    assert [trigger for _, trigger in dispatcher.enqueued] == ["fire", "reminder"]


async def test_critical_fire_records_scrubbed_audit_event(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(node_inputs(map_errors=1))
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now)

    await lane.tick()
    await lane.tick()

    async with committed_db() as db:
        events = list((await db.scalars(select(AuditEvent))).all())
    assert len(events) == 1
    assert (events[0].action, events[0].target_type, events[0].outcome) == (
        "alert.fired",
        "alert",
        "critical",
    )
    assert "secret" not in events[0].metadata


async def test_maintenance_silences_gated_rules_but_not_map_errors(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    sources = StaticSources(
        AlertInputs(
            node=NodeAlertInputs(
                map_error_count=1,
                apply_failed_count=1,
                maintenance_enabled=True,
            )
        )
    )
    dispatcher = RecordingDispatcher()
    lane = evaluator(committed_db, sources, dispatcher, now, fire_ticks=1)

    await lane.tick()

    alerts = await active_alerts(committed_db)
    assert {alert.rule_key for alert in alerts if alert.state is AlertState.firing} == {
        "map_error",
        "apply_failed",
        "bypass_or_maintenance",
    }
    rule_by_id = {alert.id: alert.rule_key for alert in alerts}
    assert {(rule_by_id[alert_id], trigger) for alert_id, trigger in dispatcher.enqueued} == {
        ("map_error", "fire"),
        ("bypass_or_maintenance", "fire"),
    }


async def test_run_loop_swallows_a_raising_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    lane = evaluator(
        session_factory=None,  # type: ignore[arg-type]
        sources=StaticSources(AlertInputs()),
        dispatcher=RecordingDispatcher(),
        now=now,
    )
    calls = 0
    stop = asyncio.Event()

    async def raise_once() -> None:
        nonlocal calls
        calls += 1
        stop.set()
        raise RuntimeError("expected test failure")

    monkeypatch.setattr(lane, "tick", raise_once)
    await lane.run_loop(stop)
    assert calls == 1
