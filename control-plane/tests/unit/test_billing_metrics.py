from decimal import Decimal

import pytest

from app.services.billing_metrics import bps_to_gbps, p95_nearest_rank

pytestmark = pytest.mark.unit


def test_p95_nearest_rank_returns_zero_for_no_samples() -> None:
    assert p95_nearest_rank([]) == 0


def test_p95_nearest_rank_returns_the_only_sample() -> None:
    assert p95_nearest_rank([42]) == 42


def test_p95_nearest_rank_uses_the_exact_rank_for_twenty_samples() -> None:
    assert p95_nearest_rank(list(range(1, 21))) == 19


def test_p95_nearest_rank_rounds_up_to_the_next_rank() -> None:
    assert p95_nearest_rank([10, 20]) == 20


def test_p95_nearest_rank_sorts_before_selecting_the_rank() -> None:
    assert (
        p95_nearest_rank(
            [21, 1, 20, 2, 19, 3, 18, 4, 17, 5, 16, 6, 15, 7, 14, 8, 13, 9, 12, 10, 11]
        )
        == 20
    )


def test_bps_to_gbps_converts_bytes_to_bits_and_quantizes_to_two_places() -> None:
    result = bps_to_gbps(1_250_000_000)

    assert result == Decimal("10.00")
    assert result.as_tuple().exponent == -2
