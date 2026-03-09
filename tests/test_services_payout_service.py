from datetime import datetime, timezone

from app.services.payout_service import previous_month_window


def test_previous_month_window():
    start, end = previous_month_window(now=datetime(2026, 2, 25, 10, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert end.year == 2026
    assert end.month == 1
    assert end.day == 31
