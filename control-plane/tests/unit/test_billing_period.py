from datetime import UTC, datetime

import pytest

from app.services.billing_period import month_period, previous_period

pytestmark = pytest.mark.unit


def test_month_period_returns_utc_bounds_for_a_mid_month_instant() -> None:
    start, end = month_period(datetime(2025, 7, 14, 12, 30, 45, tzinfo=UTC))

    assert start == datetime(2025, 7, 1, tzinfo=UTC)
    assert end == datetime(2025, 8, 1, tzinfo=UTC)
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC


def test_month_period_keeps_a_month_end_in_its_current_period() -> None:
    start, end = month_period(datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC))

    assert (start, end) == (
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 2, 1, tzinfo=UTC),
    )


def test_month_period_rolls_december_into_january() -> None:
    start, end = month_period(datetime(2025, 12, 15, tzinfo=UTC))

    assert (start, end) == (
        datetime(2025, 12, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_month_period_handles_leap_year_february() -> None:
    start, end = month_period(datetime(2024, 2, 29, 23, 59, 59, tzinfo=UTC))

    assert (start, end) == (
        datetime(2024, 2, 1, tzinfo=UTC),
        datetime(2024, 3, 1, tzinfo=UTC),
    )


def test_previous_period_is_the_inverse_of_the_following_month_start() -> None:
    current_start, current_end = month_period(datetime(2025, 1, 15, tzinfo=UTC))

    assert previous_period(current_end) == (current_start, current_end)
    assert previous_period(current_start) == (
        datetime(2024, 12, 1, tzinfo=UTC),
        current_start,
    )
