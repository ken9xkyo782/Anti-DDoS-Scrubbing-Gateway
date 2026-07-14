"""Stateful persisted-alert lifecycle worker lane."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Alert, AlertRule, AlertScope, AlertSeverity, AlertState
from app.services.alert_rules import (
    RULE_BY_KEY,
    AlertInputs,
    EffectiveRule,
    RuleObservation,
    Severity,
    evaluate,
)
from app.services.audit import record_event

logger = logging.getLogger(__name__)


class AlertSource(Protocol):
    async def load(self, db: AsyncSession, now: datetime) -> AlertInputs: ...


class NotificationDispatcher(Protocol):
    """Delivery seam; channel persistence and I/O belong to the dispatcher."""

    async def enqueue(self, db: AsyncSession, alert: Alert, trigger: str) -> None: ...

    async def dispatch_pending(self, db: AsyncSession) -> None: ...


class AlertEvaluator:
    """Reconcile pure rule observations against durable alert lifecycle state."""

    def __init__(
        self,
        *,
        sources: AlertSource,
        dispatcher: NotificationDispatcher,
        session_factory: async_sessionmaker[AsyncSession],
        fire_ticks: int,
        clear_ticks: int,
        renotify_seconds: float,
        interval_seconds: float,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.sources = sources
        self.dispatcher = dispatcher
        self.session_factory = session_factory
        self.fire_ticks = fire_ticks
        self.clear_ticks = clear_ticks
        self.renotify_seconds = renotify_seconds
        self.interval_seconds = interval_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    async def tick(self) -> None:
        """Load sources and atomically persist one lifecycle reconciliation."""
        now = self.clock()
        async with self.session_factory() as db:
            try:
                inputs = await self.sources.load(db, now)
                effective, silence_in_maintenance = await self._load_rules(db)
                observations = evaluate(inputs, effective)
                await self.reconcile(
                    db,
                    observations,
                    now=now,
                    maintenance_enabled=bool(inputs.node.maintenance_enabled),
                    effective=effective,
                    silence_in_maintenance=silence_in_maintenance,
                )
                await self.dispatcher.dispatch_pending(db)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def reconcile(
        self,
        db: AsyncSession,
        observations: list[RuleObservation],
        *,
        now: datetime,
        maintenance_enabled: bool,
        effective: Mapping[str, EffectiveRule],
        silence_in_maintenance: Mapping[str, bool],
    ) -> None:
        """Drive active alerts through pending, firing, and resolved states."""
        active_alerts = list(
            (
                await db.scalars(
                    select(Alert).where(Alert.state != AlertState.resolved).with_for_update()
                )
            ).all()
        )
        active_by_key = {(alert.rule_key, alert.scope_key): alert for alert in active_alerts}
        observed_keys = {(item.rule_key, item.scope_key) for item in observations}

        for observation in observations:
            alert = active_by_key.get((observation.rule_key, observation.scope_key))
            if alert is None:
                if observation.firing:
                    await self._open_pending(
                        db,
                        observation,
                        now,
                        maintenance_enabled,
                        silence_in_maintenance,
                    )
                continue
            await self._reconcile_observation(
                db,
                alert,
                observation,
                now,
                maintenance_enabled,
                effective,
                silence_in_maintenance,
            )

        for alert in active_alerts:
            if (alert.rule_key, alert.scope_key) not in observed_keys:
                await self._resolve(
                    db,
                    alert,
                    now,
                    maintenance_enabled,
                    silence_in_maintenance,
                )

    async def run_loop(self, stop: asyncio.Event) -> None:
        """Run until stopped; an individual faulty tick must not kill the lane."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("Alert evaluation iteration failed")

            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def _load_rules(
        self,
        db: AsyncSession,
    ) -> tuple[dict[str, EffectiveRule], dict[str, bool]]:
        persisted = {rule.key: rule for rule in (await db.scalars(select(AlertRule))).all()}
        effective: dict[str, EffectiveRule] = {}
        silence_in_maintenance: dict[str, bool] = {}
        for key, definition in RULE_BY_KEY.items():
            row = persisted.get(key)
            if row is None:
                effective[key] = EffectiveRule(
                    enabled=definition.default_enabled,
                    severity=definition.severity,
                    fire_threshold=definition.fire_threshold,
                    clear_threshold=definition.clear_threshold,
                )
                silence_in_maintenance[key] = definition.silence_in_maintenance
                continue
            effective[key] = EffectiveRule(
                enabled=row.enabled,
                severity=Severity(row.severity_override.value)
                if row.severity_override is not None
                else definition.severity,
                fire_threshold=float(row.fire_threshold_override)
                if row.fire_threshold_override is not None
                else definition.fire_threshold,
                clear_threshold=float(row.clear_threshold_override)
                if row.clear_threshold_override is not None
                else definition.clear_threshold,
            )
            silence_in_maintenance[key] = row.silence_in_maintenance
        return effective, silence_in_maintenance

    async def _open_pending(
        self,
        db: AsyncSession,
        observation: RuleObservation,
        now: datetime,
        maintenance_enabled: bool,
        silence_in_maintenance: Mapping[str, bool],
    ) -> None:
        alert = Alert(
            rule_key=observation.rule_key,
            scope=AlertScope(observation.scope.value),
            scope_key=observation.scope_key,
            tenant_id=observation.tenant_id,
            service_id=observation.service_id,
            severity=AlertSeverity(observation.severity.value),
            state=AlertState.pending,
            metric_value=Decimal(str(observation.metric_value)),
            context=observation.context,
            fire_streak=1,
            clear_streak=0,
            first_observed_at=now,
        )
        db.add(alert)
        await db.flush()
        if self.fire_ticks <= 1:
            await self._fire(
                db,
                alert,
                now,
                maintenance_enabled,
                silence_in_maintenance,
            )

    async def _reconcile_observation(
        self,
        db: AsyncSession,
        alert: Alert,
        observation: RuleObservation,
        now: datetime,
        maintenance_enabled: bool,
        effective: Mapping[str, EffectiveRule],
        silence_in_maintenance: Mapping[str, bool],
    ) -> None:
        was_critical = alert.severity is AlertSeverity.critical
        alert.metric_value = Decimal(str(observation.metric_value))
        alert.context = observation.context
        alert.severity = AlertSeverity(observation.severity.value)

        if observation.firing:
            alert.clear_streak = 0
            if alert.state is AlertState.pending:
                alert.fire_streak += 1
                if alert.fire_streak >= self.fire_ticks:
                    await self._fire(
                        db,
                        alert,
                        now,
                        maintenance_enabled,
                        silence_in_maintenance,
                    )
                return
            if alert.state is AlertState.firing:
                if not was_critical and alert.severity is AlertSeverity.critical:
                    await self._audit_critical(db, alert)
                if self._renotify_due(alert, now):
                    await self._enqueue(
                        db,
                        alert,
                        "reminder",
                        now,
                        maintenance_enabled,
                        silence_in_maintenance,
                    )
            return

        if alert.state is AlertState.pending:
            alert.fire_streak = 0
            alert.clear_streak = 0
            return

        if self._in_hysteresis_band(observation, effective.get(observation.rule_key)):
            alert.clear_streak = 0
            return
        alert.clear_streak += 1
        if alert.clear_streak >= self.clear_ticks:
            await self._resolve(
                db,
                alert,
                now,
                maintenance_enabled,
                silence_in_maintenance,
            )

    async def _fire(
        self,
        db: AsyncSession,
        alert: Alert,
        now: datetime,
        maintenance_enabled: bool,
        silence_in_maintenance: Mapping[str, bool],
    ) -> None:
        alert.state = AlertState.firing
        alert.fired_at = now
        alert.clear_streak = 0
        await self._enqueue(
            db,
            alert,
            "fire",
            now,
            maintenance_enabled,
            silence_in_maintenance,
        )
        if alert.severity is AlertSeverity.critical:
            await self._audit_critical(db, alert)

    async def _resolve(
        self,
        db: AsyncSession,
        alert: Alert,
        now: datetime,
        maintenance_enabled: bool,
        silence_in_maintenance: Mapping[str, bool],
    ) -> None:
        if alert.state is AlertState.resolved:
            return
        alert.state = AlertState.resolved
        alert.resolved_at = now
        await self._enqueue(
            db,
            alert,
            "resolve",
            now,
            maintenance_enabled,
            silence_in_maintenance,
        )

    async def _enqueue(
        self,
        db: AsyncSession,
        alert: Alert,
        trigger: str,
        now: datetime,
        maintenance_enabled: bool,
        silence_in_maintenance: Mapping[str, bool],
    ) -> None:
        if maintenance_enabled and silence_in_maintenance.get(alert.rule_key, False):
            return
        alert.last_notified_at = now
        await self.dispatcher.enqueue(db, alert, trigger)

    async def _audit_critical(self, db: AsyncSession, alert: Alert) -> None:
        await record_event(
            db,
            actor=None,
            action="alert.fired",
            target_type="alert",
            target_id=str(alert.id),
            outcome="critical",
            metadata={
                "rule_key": alert.rule_key,
                "scope_key": alert.scope_key,
                "severity": alert.severity.value,
                "metric_value": str(alert.metric_value) if alert.metric_value is not None else None,
                "context": alert.context,
            },
        )

    def _renotify_due(self, alert: Alert, now: datetime) -> bool:
        if alert.acknowledged_at is not None or alert.last_notified_at is None:
            return False
        return now - alert.last_notified_at >= timedelta(seconds=self.renotify_seconds)

    @staticmethod
    def _in_hysteresis_band(
        observation: RuleObservation,
        effective: EffectiveRule | None,
    ) -> bool:
        if effective is None:
            return False
        if effective.fire_threshold == effective.clear_threshold:
            return False
        assert effective.clear_threshold is not None
        return observation.metric_value >= effective.clear_threshold
