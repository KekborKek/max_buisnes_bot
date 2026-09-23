"""T6 (#50): форматирование дат и чисел для текстов бота — app/bot/formatting.py.

Даты — ТЕСТОВЫЕ ДАННЫЕ, не налоговые сроки. Ожидаемые строки записаны явно.
"""

from datetime import date

import pytest

from app.bot.formatting import (
    count_words,
    date_with_shift,
    format_date,
    join_titles,
    plural,
    when_words,
)

TODAY = date(2026, 10, 21)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 28), "28 октября"),
        (date(2026, 1, 1), "1 января"),
        (date(2026, 12, 31), "31 декабря"),
        (date(2027, 4, 26), "26 апреля 2027"),
        (date(2025, 3, 5), "5 марта 2025"),
    ],
)
def test_format_date_year_only_if_not_current(day, expected):
    assert format_date(day, TODAY) == expected


def test_shift_note_after_date_only_when_moved():
    assert (
        date_with_shift(date(2026, 10, 26), date(2026, 10, 25), TODAY)
        == "26 октября (перенос с 25 октября, выходной)"
    )
    assert date_with_shift(date(2026, 10, 28), date(2026, 10, 28), TODAY) == "28 октября"
    assert date_with_shift(date(2026, 10, 28), None, TODAY) == "28 октября"


def test_shift_note_across_year():
    assert (
        date_with_shift(date(2027, 1, 11), date(2026, 12, 28), TODAY)
        == "11 января 2027 (перенос с 28 декабря, выходной)"
    )


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "Сегодня"),
        (1, "Завтра"),
        (2, "Через 2 дня"),
        (3, "Через 3 дня"),
        (5, "Через 5 дней"),
        (7, "Через 7 дней"),
        (11, "Через 11 дней"),
        (21, "Через 21 день"),
        (22, "Через 22 дня"),
        (30, "Через месяц"),
        (-1, "Сегодня"),
    ],
)
def test_when_words(days, expected):
    from datetime import timedelta

    assert when_words(TODAY + timedelta(days=days), TODAY) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(2, "два срока"), (3, "три срока"), (4, "четыре срока"), (5, "5 сроков"), (21, "21 срок")],
)
def test_count_words(n, expected):
    assert count_words(n) == expected


@pytest.mark.parametrize(
    ("titles", "expected"),
    [
        (["А"], "А"),
        (["А", "Б"], "А и Б"),
        (["А", "Б", "В"], "А, Б и В"),
    ],
)
def test_join_titles(titles, expected):
    assert join_titles(titles) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "a"), (2, "b"), (4, "b"), (5, "c"), (11, "c"), (12, "c"), (14, "c"), (101, "a")],
)
def test_plural(n, expected):
    assert plural(n, "a", "b", "c") == expected
