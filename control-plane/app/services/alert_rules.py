"""Fixed alert-rule catalog and pure per-tick predicates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class AlertScope(StrEnum):
    node = "node"
    service = "service"


class Severity(StrEnum):
    info = "info"
    warning = "warning"
    critical = "critical"


MetricValue = int | float
ContextValue = str | int | float | bool


@dataclass(frozen=True)
class RuleDef:
    key: str
    scope: AlertScope
    severity: Severity
    fire_threshold: MetricValue
    clear_threshold: MetricValue
    default_enabled: bool = True
    silence_in_maintenance: bool = False
    title: str = ""
    critical_threshold: MetricValue | None = None


@dataclass(frozen=True)
class EffectiveRule:
    """Optional persisted overrides; absent values fall back to the catalog."""

    enabled: bool | None = None
    severity: Severity | None = None
    fire_threshold: MetricValue | None = None
    clear_threshold: MetricValue | None = None


@dataclass(frozen=True)
class NodeAlertInputs:
    map_error_count: int | None = None
    xdp_mode: str | None = None
    node_clean_bps: int | None = None
    node_capacity_bps: int | None = None
    apply_failed_count: int | None = None
    job_backlog: int | None = None
    stuck_applying: bool | None = None
    telemetry_stale: bool | None = None
    feed_failure_count: int | None = None
    bloom_false_positives: int | None = None
    bypass_enabled: bool | None = None
    maintenance_enabled: bool | None = None


@dataclass(frozen=True)
class ServiceAlertInputs:
    scope_key: str
    tenant_id: UUID
    service_id: UUID
    clean_bps: int | None = None
    committed_bps: int | None = None
    drop_bps: int | None = None
    total_bps: int | None = None
    whitelist_overlap_count: int | None = None


@dataclass(frozen=True)
class AlertInputs:
    node: NodeAlertInputs = field(default_factory=NodeAlertInputs)
    services: tuple[ServiceAlertInputs, ...] = ()


@dataclass(frozen=True)
class RuleObservation:
    rule_key: str
    scope: AlertScope
    scope_key: str
    tenant_id: UUID | None
    service_id: UUID | None
    severity: Severity
    metric_value: MetricValue
    firing: bool
    context: dict[str, ContextValue]


RULES: tuple[RuleDef, ...] = (
    RuleDef(
        key="map_error",
        scope=AlertScope.node,
        severity=Severity.critical,
        fire_threshold=0,
        clear_threshold=0,
        title="Data-plane map error",
    ),
    RuleDef(
        key="xdp_degraded",
        scope=AlertScope.node,
        severity=Severity.critical,
        fire_threshold=1,
        clear_threshold=0,
        title="XDP mode degraded",
    ),
    RuleDef(
        key="near_capacity",
        scope=AlertScope.node,
        severity=Severity.warning,
        fire_threshold=0.9,
        clear_threshold=0.85,
        critical_threshold=1.0,
        title="Node clean throughput near capacity",
    ),
    RuleDef(
        key="apply_failed",
        scope=AlertScope.node,
        severity=Severity.critical,
        fire_threshold=1,
        clear_threshold=0,
        silence_in_maintenance=True,
        title="Configuration apply failed",
    ),
    RuleDef(
        key="worker_backlog",
        scope=AlertScope.node,
        severity=Severity.warning,
        fire_threshold=100,
        clear_threshold=80,
        silence_in_maintenance=True,
        title="Worker backlog or progress failure",
    ),
    RuleDef(
        key="feed_failed",
        scope=AlertScope.node,
        severity=Severity.warning,
        fire_threshold=1,
        clear_threshold=0,
        title="Threat-feed sync failed",
    ),
    RuleDef(
        key="committed_not_honored",
        scope=AlertScope.service,
        severity=Severity.warning,
        fire_threshold=1,
        clear_threshold=1,
        title="Committed clean throughput not honored",
    ),
    RuleDef(
        key="attack_onset",
        scope=AlertScope.service,
        severity=Severity.warning,
        fire_threshold=0.1,
        clear_threshold=0.05,
        title="Service drop share indicates attack onset",
    ),
    RuleDef(
        key="bloom_false_positive",
        scope=AlertScope.node,
        severity=Severity.warning,
        fire_threshold=1_000,
        clear_threshold=900,
        title="Bloom false-positive volume high",
    ),
    RuleDef(
        key="bypass_or_maintenance",
        scope=AlertScope.node,
        severity=Severity.critical,
        fire_threshold=1,
        clear_threshold=0,
        title="Bypass or maintenance active",
    ),
    RuleDef(
        key="whitelist_overlap",
        scope=AlertScope.service,
        severity=Severity.warning,
        fire_threshold=1,
        clear_threshold=0,
        title="Whitelist overlaps a threat-feed entry",
    ),
)

RULE_BY_KEY = {rule.key: rule for rule in RULES}
DEFAULT_THRESHOLDS = {rule.key: (rule.fire_threshold, rule.clear_threshold) for rule in RULES}


def evaluate(
    inputs: AlertInputs,
    effective: Mapping[str, EffectiveRule],
) -> list[RuleObservation]:
    """Evaluate every enabled rule against a preloaded immutable input snapshot."""

    observations: list[RuleObservation] = []
    for rule in RULES:
        resolved = _resolved_rule(rule, effective.get(rule.key))
        if not resolved.enabled:
            continue
        if rule.scope is AlertScope.node:
            observation = _evaluate_node(rule, resolved, inputs.node)
            if observation is not None:
                observations.append(observation)
            continue
        for service in inputs.services:
            observation = _evaluate_service(rule, resolved, service)
            if observation is not None:
                observations.append(observation)
    return observations


def _resolved_rule(rule: RuleDef, override: EffectiveRule | None) -> EffectiveRule:
    if override is None:
        return EffectiveRule(
            enabled=rule.default_enabled,
            severity=rule.severity,
            fire_threshold=rule.fire_threshold,
            clear_threshold=rule.clear_threshold,
        )
    return EffectiveRule(
        enabled=rule.default_enabled if override.enabled is None else override.enabled,
        severity=rule.severity if override.severity is None else override.severity,
        fire_threshold=(
            rule.fire_threshold if override.fire_threshold is None else override.fire_threshold
        ),
        clear_threshold=(
            rule.clear_threshold if override.clear_threshold is None else override.clear_threshold
        ),
    )


def _evaluate_node(
    rule: RuleDef,
    effective: EffectiveRule,
    node: NodeAlertInputs,
) -> RuleObservation | None:
    if rule.key == "map_error" and node.map_error_count is not None:
        return _node_observation(
            rule,
            effective,
            node.map_error_count,
            node.map_error_count > _threshold(effective),
        )
    if rule.key == "xdp_degraded" and node.xdp_mode is not None:
        degraded = node.xdp_mode.lower() in {"generic", "offline", "off", "detached"}
        return _node_observation(rule, effective, int(degraded), degraded)
    if rule.key == "near_capacity":
        if (
            node.node_clean_bps is None
            or node.node_capacity_bps is None
            or node.node_capacity_bps <= 0
        ):
            return None
        ratio = node.node_clean_bps / node.node_capacity_bps
        severity = effective.severity
        if rule.critical_threshold is not None and ratio >= rule.critical_threshold:
            severity = Severity.critical
        return _node_observation(rule, effective, ratio, ratio >= _threshold(effective), severity)
    if rule.key == "apply_failed" and node.apply_failed_count is not None:
        return _node_observation(
            rule,
            effective,
            node.apply_failed_count,
            node.apply_failed_count >= _threshold(effective),
        )
    if rule.key == "worker_backlog":
        return _worker_health_observation(rule, effective, node)
    if rule.key == "feed_failed" and node.feed_failure_count is not None:
        return _node_observation(
            rule,
            effective,
            node.feed_failure_count,
            node.feed_failure_count >= _threshold(effective),
        )
    if rule.key == "bloom_false_positive" and node.bloom_false_positives is not None:
        return _node_observation(
            rule,
            effective,
            node.bloom_false_positives,
            node.bloom_false_positives >= _threshold(effective),
        )
    if rule.key == "bypass_or_maintenance":
        if node.bypass_enabled is None and node.maintenance_enabled is None:
            return None
        active = bool(node.bypass_enabled) or bool(node.maintenance_enabled)
        return _node_observation(rule, effective, int(active), active)
    return None


def _evaluate_service(
    rule: RuleDef,
    effective: EffectiveRule,
    service: ServiceAlertInputs,
) -> RuleObservation | None:
    if rule.key == "committed_not_honored":
        if service.clean_bps is None or service.committed_bps is None or service.committed_bps <= 0:
            return None
        return _service_observation(
            rule,
            effective,
            service,
            service.clean_bps,
            service.clean_bps < service.committed_bps,
        )
    if rule.key == "attack_onset":
        if service.drop_bps is None or service.total_bps is None or service.total_bps <= 0:
            return None
        drop_share = service.drop_bps / service.total_bps
        return _service_observation(
            rule,
            effective,
            service,
            drop_share,
            drop_share >= _threshold(effective),
        )
    if rule.key == "whitelist_overlap" and service.whitelist_overlap_count is not None:
        return _service_observation(
            rule,
            effective,
            service,
            service.whitelist_overlap_count,
            service.whitelist_overlap_count >= _threshold(effective),
        )
    return None


def _worker_health_observation(
    rule: RuleDef,
    effective: EffectiveRule,
    node: NodeAlertInputs,
) -> RuleObservation | None:
    conditions: list[tuple[MetricValue, bool]] = []
    if node.job_backlog is not None:
        conditions.append((node.job_backlog, node.job_backlog >= _threshold(effective)))
    if node.stuck_applying is not None:
        conditions.append((int(node.stuck_applying), node.stuck_applying))
    if node.telemetry_stale is not None:
        conditions.append((int(node.telemetry_stale), node.telemetry_stale))
    if not conditions:
        return None
    metric_value, _ = max(conditions, key=lambda condition: int(condition[1]))
    firing = any(condition[1] for condition in conditions)
    return _node_observation(rule, effective, metric_value, firing)


def _node_observation(
    rule: RuleDef,
    effective: EffectiveRule,
    metric_value: MetricValue,
    firing: bool,
    severity: Severity | None = None,
) -> RuleObservation:
    return RuleObservation(
        rule_key=rule.key,
        scope=AlertScope.node,
        scope_key="node",
        tenant_id=None,
        service_id=None,
        severity=_severity(effective) if severity is None else severity,
        metric_value=metric_value,
        firing=firing,
        context={"title": rule.title},
    )


def _service_observation(
    rule: RuleDef,
    effective: EffectiveRule,
    service: ServiceAlertInputs,
    metric_value: MetricValue,
    firing: bool,
) -> RuleObservation:
    return RuleObservation(
        rule_key=rule.key,
        scope=AlertScope.service,
        scope_key=service.scope_key,
        tenant_id=service.tenant_id,
        service_id=service.service_id,
        severity=_severity(effective),
        metric_value=metric_value,
        firing=firing,
        context={"title": rule.title},
    )


def _threshold(effective: EffectiveRule) -> MetricValue:
    assert effective.fire_threshold is not None
    return effective.fire_threshold


def _severity(effective: EffectiveRule) -> Severity:
    assert effective.severity is not None
    return effective.severity
