from uuid import uuid4

import pytest

from app.services.alert_rules import (
    RULES,
    AlertInputs,
    AlertScope,
    EffectiveRule,
    NodeAlertInputs,
    ServiceAlertInputs,
    Severity,
    evaluate,
)

pytestmark = pytest.mark.unit


def _inputs(**node_values: int | float | str | bool | None) -> AlertInputs:
    return AlertInputs(node=NodeAlertInputs(**node_values))


def _service_inputs(**service_values: int | float | bool | None) -> AlertInputs:
    return AlertInputs(
        services=(
            ServiceAlertInputs(
                scope_key="42",
                tenant_id=uuid4(),
                service_id=uuid4(),
                **service_values,
            ),
        )
    )


@pytest.mark.parametrize(
    ("rule_key", "inputs"),
    [
        ("map_error", _inputs(map_error_count=1)),
        ("xdp_degraded", _inputs(xdp_mode="generic")),
        ("near_capacity", _inputs(node_clean_bps=900, node_capacity_bps=1_000)),
        ("apply_failed", _inputs(apply_failed_count=1)),
        ("worker_backlog", _inputs(job_backlog=100)),
        ("feed_failed", _inputs(feed_failure_count=1)),
        ("committed_not_honored", _service_inputs(clean_bps=999, committed_bps=1_000)),
        ("attack_onset", _service_inputs(drop_bps=100, total_bps=1_000)),
        ("bloom_false_positive", _inputs(bloom_false_positives=1_000)),
        ("bypass_or_maintenance", _inputs(bypass_enabled=True)),
        ("whitelist_overlap", _service_inputs(whitelist_overlap_count=1)),
    ],
)
def test_each_catalog_rule_fires_for_its_in_scope_subject(
    rule_key: str,
    inputs: AlertInputs,
) -> None:
    observations = evaluate(inputs, {})

    observation = next(item for item in observations if item.rule_key == rule_key)

    assert observation.firing is True


@pytest.mark.parametrize(
    ("rule_key", "inputs"),
    [
        ("map_error", _inputs(map_error_count=0)),
        ("xdp_degraded", _inputs(xdp_mode="native")),
        ("near_capacity", _inputs(node_clean_bps=899, node_capacity_bps=1_000)),
        ("apply_failed", _inputs(apply_failed_count=0)),
        ("worker_backlog", _inputs(job_backlog=99)),
        ("feed_failed", _inputs(feed_failure_count=0)),
        ("committed_not_honored", _service_inputs(clean_bps=1_000, committed_bps=1_000)),
        ("attack_onset", _service_inputs(drop_bps=99, total_bps=1_000)),
        ("bloom_false_positive", _inputs(bloom_false_positives=999)),
        ("bypass_or_maintenance", _inputs(bypass_enabled=False, maintenance_enabled=False)),
        ("whitelist_overlap", _service_inputs(whitelist_overlap_count=0)),
    ],
)
def test_each_catalog_rule_does_not_fire_below_its_clear_threshold(
    rule_key: str,
    inputs: AlertInputs,
) -> None:
    observations = evaluate(inputs, {})

    observation = next(item for item in observations if item.rule_key == rule_key)

    assert observation.firing is False


def test_rule_catalog_has_all_fixed_events_and_defaults() -> None:
    rules = {rule.key: rule for rule in RULES}

    assert len(rules) == 11
    assert rules["map_error"].severity is Severity.critical
    assert rules["map_error"].fire_threshold == 0
    assert rules["near_capacity"].fire_threshold == 0.9
    assert rules["near_capacity"].critical_threshold == 1.0
    assert rules["committed_not_honored"].severity is Severity.warning
    assert rules["bloom_false_positive"].fire_threshold == 1_000
    assert all(rule.default_enabled for rule in RULES)


def test_near_capacity_escalates_between_warning_and_critical_bands() -> None:
    warning = evaluate(_inputs(node_clean_bps=900, node_capacity_bps=1_000), {})
    critical = evaluate(_inputs(node_clean_bps=1_000, node_capacity_bps=1_000), {})

    warning_observation = next(item for item in warning if item.rule_key == "near_capacity")
    critical_observation = next(item for item in critical if item.rule_key == "near_capacity")

    assert warning_observation.severity is Severity.warning
    assert critical_observation.severity is Severity.critical


def test_disabled_rule_produces_no_observation() -> None:
    observations = evaluate(
        _inputs(map_error_count=1),
        {"map_error": EffectiveRule(enabled=False)},
    )

    assert all(observation.rule_key != "map_error" for observation in observations)


def test_effective_rule_overrides_the_catalog_default_threshold() -> None:
    observations = evaluate(
        _inputs(map_error_count=1),
        {"map_error": EffectiveRule(fire_threshold=1)},
    )

    observation = next(item for item in observations if item.rule_key == "map_error")

    assert observation.firing is False


def test_service_observations_preserve_owner_and_node_observations_do_not() -> None:
    tenant_id = uuid4()
    service_id = uuid4()
    inputs = AlertInputs(
        node=NodeAlertInputs(map_error_count=1),
        services=(
            ServiceAlertInputs(
                scope_key="42",
                tenant_id=tenant_id,
                service_id=service_id,
                clean_bps=1,
                committed_bps=2,
            ),
        ),
    )

    observations = evaluate(inputs, {})
    node_observation = next(item for item in observations if item.rule_key == "map_error")
    service_observation = next(
        item for item in observations if item.rule_key == "committed_not_honored"
    )

    assert node_observation.scope is AlertScope.node
    assert node_observation.tenant_id is None
    assert node_observation.service_id is None
    assert service_observation.scope is AlertScope.service
    assert service_observation.tenant_id == tenant_id
    assert service_observation.service_id == service_id
