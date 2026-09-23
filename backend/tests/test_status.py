"""T10: статус события считает бэкенд в часовом поясе пользователя (product.md)."""

from datetime import UTC, date, datetime

import pytest

from app.calendar.status import item_status, today_in

DUE = date(2026, 10, 28)


@pytest.mark.parametrize(
    ("today", "done_at", "expected"),
    [
        (date(2026, 10, 27), None, "upcoming"),
        (date(2026, 10, 28), None, "today"),
        (date(2026, 10, 29), None, "overdue"),
        (date(2026, 10, 29), datetime(2026, 10, 30, tzinfo=UTC), "done"),
        (date(2026, 10, 1), datetime(2026, 9, 30, tzinfo=UTC), "done"),
    ],
)
def test_item_status(today, done_at, expected):
    assert item_status(DUE, done_at, today) == expected


def test_vladivostok_day_boundary():
    """27.10 14:00 UTC — в Москве ещё 27-е, во Владивостоке (UTC+10) уже 28-е, день срока."""
    now = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
    moscow = today_in("Europe/Moscow", now)
    vladivostok = today_in("Asia/Vladivostok", now)
    assert moscow == date(2026, 10, 27)
    assert vladivostok == date(2026, 10, 28)
    assert item_status(DUE, None, moscow) == "upcoming"
    assert item_status(DUE, None, vladivostok) == "today"


def test_vladivostok_just_before_midnight():
    """13:59 UTC — во Владивостоке 23:59 27-го: срок ещё не наступил."""
    now = datetime(2026, 10, 27, 13, 59, tzinfo=UTC)
    assert today_in("Asia/Vladivostok", now) == date(2026, 10, 27)


@pytest.mark.parametrize("tz", [None, "", "Mars/Olympus"])
def test_unknown_timezone_falls_back_to_moscow(tz):
    now = datetime(2026, 10, 27, 21, 30, tzinfo=UTC)  # 00:30 28-го по Москве
    assert today_in(tz, now) == date(2026, 10, 28)
