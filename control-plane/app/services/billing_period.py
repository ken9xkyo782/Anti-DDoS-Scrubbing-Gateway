from datetime import UTC, datetime


def month_period(at: datetime) -> tuple[datetime, datetime]:
    at_utc = at.astimezone(UTC)
    period_start = datetime(at_utc.year, at_utc.month, 1, tzinfo=UTC)

    if at_utc.month == 12:
        period_end = datetime(at_utc.year + 1, 1, 1, tzinfo=UTC)
    else:
        period_end = datetime(at_utc.year, at_utc.month + 1, 1, tzinfo=UTC)

    return period_start, period_end


def previous_period(period_start: datetime) -> tuple[datetime, datetime]:
    current_start, _ = month_period(period_start)

    if current_start.month == 1:
        previous_start = datetime(current_start.year - 1, 12, 1, tzinfo=UTC)
    else:
        previous_start = datetime(current_start.year, current_start.month - 1, 1, tzinfo=UTC)

    return previous_start, current_start
