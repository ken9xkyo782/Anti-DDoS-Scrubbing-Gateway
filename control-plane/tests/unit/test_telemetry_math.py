from decimal import Decimal

import pytest

from app.db.models import ServicePlan
from app.services.telemetry_math import committed_clean_bps, committed_honored

pytestmark = pytest.mark.unit


def test_committed_clean_bps_and_honored_return_none_plan_defaults() -> None:
    assert committed_clean_bps(None) == 0
    assert committed_honored(0, None) is None


def test_committed_clean_bps_converts_gigabits_to_bits_per_second() -> None:
    plan = ServicePlan(
        committed_clean_gbps=Decimal("10"),
        ceiling_clean_gbps=Decimal("10"),
    )

    assert committed_clean_bps(plan) == 10_000_000_000


@pytest.mark.parametrize(
    ("bps", "honored"),
    [
        (10_000_000_000, True),
        (9_999_999_999, False),
    ],
)
def test_committed_honored_uses_committed_bps_boundary(bps: int, honored: bool) -> None:
    plan = ServicePlan(
        committed_clean_gbps=Decimal("10"),
        ceiling_clean_gbps=Decimal("10"),
    )

    assert committed_honored(bps, plan) is honored
