import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.worker.__main__ import build_telemetry_aggregator
from app.worker.telemetry import TelemetryAggregator


@pytest.mark.unit
def test_telemetry_settings_have_safe_defaults_and_an_exact_cadence() -> None:
    settings = Settings()

    assert settings.worker_telemetry_enabled is True
    assert settings.worker_telemetry_interval_seconds == 2
    assert settings.worker_telemetry_retention_seconds == 7 * 24 * 60 * 60
    assert settings.worker_telemetry_binary_path == "../data-plane/build/dpstat"
    assert settings.worker_telemetry_ifindex is None
    assert settings.worker_telemetry_timeout_seconds == 5.0

    with pytest.raises(ValidationError):
        Settings(worker_telemetry_interval_seconds=0)
    with pytest.raises(ValidationError):
        Settings(worker_telemetry_interval_seconds=3)


@pytest.mark.unit
def test_build_telemetry_aggregator_respects_the_enable_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()
    enabled = Settings(worker_telemetry_ifindex=17)
    captured: dict[str, object] = {}

    class RecordingReader:
        def __init__(self, **values: object) -> None:
            captured.update(values)

    monkeypatch.setattr("app.worker.__main__.TelemetryReader", RecordingReader)

    aggregator = build_telemetry_aggregator(
        enabled,
        session_factory,  # type: ignore[arg-type]
    )

    assert isinstance(aggregator, TelemetryAggregator)
    assert aggregator.interval_seconds == 2
    assert aggregator.retention_seconds == 7 * 24 * 60 * 60
    assert aggregator.node_clean_capacity_bps == 40_000_000_000
    assert captured == {
        "binary": "../data-plane/build/dpstat",
        "ifindex": 17,
        "timeout_seconds": 5.0,
    }
    assert (
        build_telemetry_aggregator(
            Settings(worker_telemetry_enabled=False),
            session_factory,  # type: ignore[arg-type]
        )
        is None
    )
