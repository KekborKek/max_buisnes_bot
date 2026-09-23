"""T3 (#40): движок дат — правило → дата, перенос на рабочий день, nds_payer.

Все календари, пороги и записи здесь — ТЕСТОВЫЕ ДАННЫЕ: придуманы для проверки логики,
это не производственный календарь, не справочник аналитика и не налоговые сроки.
Ожидаемые даты записаны явно (таблицами), а не вычислены той же формулой, что в коде.
"""

import ast
import inspect
from datetime import date

import pytest

from app.calendar import dates
from app.calendar.dates import (
    apply_shift,
    is_workday,
    nds_limit_for,
    nds_payer,
    next_workday,
    obligation_dates,
    rule_dates,
)
from app.calendar.types import (
    INCOME_BAND_BOUNDS_RUB,
    AppliesIf,
    DateRule,
    DueDate,
    HowtoLink,
    MissingYearError,
    MonthlyInQuarterRule,
    NdsConfig,
    NdsThreshold,
    Obligation,
    QuarterlyRule,
    WorkdayCalendar,
    YearlyRule,
    YearWorkdays,
)


def d(iso: str) -> date:
    return date.fromisoformat(iso)


def _days(*isos: str) -> frozenset[date]:
    return frozenset(d(x) for x in isos)


# --- ТЕСТОВЫЕ ДАННЫЕ: выдуманный производственный календарь ------------------------------------
# Дни недели для справки: 2026-01-01 — чт, 2027-01-01 — пт, 2028-01-01 — сб (2028 високосный).
CAL = WorkdayCalendar(
    years={
        2026: YearWorkdays(
            holidays=_days(
                "2026-02-10",  # вт — одиночный праздник в будни
                "2026-03-05",  # чт ┐
                "2026-03-06",  # пт ├ цепочка праздников через выходные 7–8 марта → 10 марта
                "2026-03-09",  # пн ┘
                "2026-04-28",  # вт — праздник на 28-е
                "2026-05-15",  # пт — праздник перед рабочей субботой 16 мая
                "2026-06-27",  # сб — праздник, выпавший на выходной
                "2026-10-26",  # пн — праздник сразу после выходных 24–25 октября
                "2026-12-31",  # чт — перенос уходит в январь 2027
            ),
            workdays=_days(
                "2026-05-16",  # сб — перенесённая рабочая суббота
                "2026-11-29",  # вс — перенесённое рабочее воскресенье
            ),
        ),
        2027: YearWorkdays(
            holidays=_days(
                "2027-01-01",  # пт ┐ 31.12.2026 → 1, 2, 3, 4, 5 января нерабочие → 6 января
                "2027-01-04",  # пн │
                "2027-01-05",  # вт ┘
                "2027-04-26",  # пн — праздник после выходных 24–25 апреля
            ),
            workdays=frozenset(),
        ),
        2028: YearWorkdays(
            holidays=_days("2028-02-29"),  # вт — праздник на 29 февраля високосного года
            workdays=frozenset(),
        ),
    }
)

# ТЕСТОВЫЕ ДАННЫЕ: календарь только на 2026 год — следующего года нет.
CAL_ONLY_2026 = WorkdayCalendar(years={2026: CAL.years[2026]})

# ТЕСТОВЫЕ ДАННЫЕ: длины месяцев таблицей, независимо от кода.
MONTH_LEN = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
MONTH_LEN_LEAP = {**MONTH_LEN, 2: 29}
YEARS = [(2026, MONTH_LEN), (2028, MONTH_LEN_LEAP)]


def _expected(year: int, month: int, day: int, lengths: dict[int, int]) -> date:
    return date(year, month, day if day <= lengths[month] else lengths[month])


def _obligation(rule: DateRule, shift: str = "next_workday") -> Obligation:
    """ТЕСТОВЫЕ ДАННЫЕ: выдуманная запись, не налоговый срок."""
    return Obligation(
        id="test_dates",
        title="Тестовое обязательство",
        category="taxes",
        date_rule=rule,
        shift=shift,  # type: ignore[arg-type]
        applies_if=AppliesIf(),
        needs_prep=False,
        norm="Тестовая норма",
        source_url="https://example.invalid/norm",
        howto_steps=("Шаг 1", "Шаг 2", "Шаг 3"),
        howto_link=HowtoLink(label="Открыть", url="https://example.invalid"),
        penalty_text="Тестовый текст",
        rule_version=1,
        last_checked_at=date(2030, 1, 1),
    )


# --- is_workday ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ("2026-01-28", True),  # ср
        ("2026-01-30", True),  # пт
        ("2026-01-31", False),  # сб
        ("2026-02-01", False),  # вс
        ("2026-02-02", True),  # пн
        ("2026-02-10", False),  # праздник во вторник
        ("2026-02-11", True),  # ср после праздника
        ("2026-03-05", False),  # цепочка
        ("2026-03-06", False),
        ("2026-03-09", False),
        ("2026-03-10", True),
        ("2026-05-15", False),  # праздник в пятницу
        ("2026-05-16", True),  # рабочая суббота
        ("2026-05-17", False),  # воскресенье после рабочей субботы — обычный выходной
        ("2026-06-27", False),  # праздник в субботу
        ("2026-06-28", False),  # вс
        ("2026-11-28", False),  # сб
        ("2026-11-29", True),  # рабочее воскресенье
        ("2026-12-31", False),  # праздник в четверг
        ("2027-01-01", False),
        ("2027-01-06", True),
        ("2027-12-31", True),  # пт, не праздник в 2027
        ("2028-02-29", False),  # праздник 29 февраля
        ("2028-03-01", True),
    ],
)
def test_is_workday(day, expected):
    assert is_workday(d(day), CAL) is expected


@pytest.mark.parametrize("day", ["2025-12-31", "2029-01-01", "2031-06-02"])
def test_is_workday_missing_year(day):
    with pytest.raises(MissingYearError) as exc:
        is_workday(d(day), CAL)
    assert exc.value.year == d(day).year


# --- next_workday -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        ("2026-01-28", "2026-01-28"),  # рабочий — сам день
        ("2026-01-31", "2026-02-02"),  # с субботы
        ("2026-02-01", "2026-02-02"),  # с воскресенья
        ("2026-02-10", "2026-02-11"),  # с праздника
        ("2026-03-05", "2026-03-10"),  # цепочка праздников через выходные
        ("2026-03-06", "2026-03-10"),
        ("2026-03-07", "2026-03-10"),
        ("2026-03-08", "2026-03-10"),
        ("2026-03-09", "2026-03-10"),
        ("2026-05-15", "2026-05-16"),  # праздник → перенесённая рабочая суббота
        ("2026-05-16", "2026-05-16"),  # рабочая суббота — сама
        ("2026-05-17", "2026-05-18"),
        ("2026-06-27", "2026-06-29"),  # праздник в субботу
        ("2026-10-24", "2026-10-27"),  # выходные + праздник в понедельник
        ("2026-10-25", "2026-10-27"),
        ("2026-11-28", "2026-11-29"),  # суббота → рабочее воскресенье
        ("2026-12-31", "2027-01-06"),  # 31 декабря → январь, через праздники и выходные
        ("2027-01-02", "2027-01-06"),
        ("2027-04-25", "2027-04-27"),
        ("2027-12-25", "2027-12-27"),
        ("2027-12-31", "2027-12-31"),
        ("2028-02-29", "2028-03-01"),  # праздник 29 февраля → 1 марта
    ],
)
def test_next_workday(day, expected):
    assert next_workday(d(day), CAL) == d(expected)


def test_next_workday_stays_in_year_without_next_year():
    """Следующего года нет, но он и не нужен."""
    assert next_workday(d("2026-12-26"), CAL_ONLY_2026) == d("2026-12-28")
    assert next_workday(d("2026-12-30"), CAL_ONLY_2026) == d("2026-12-30")


@pytest.mark.parametrize(
    ("cal", "day", "missing"),
    [
        (CAL_ONLY_2026, "2026-12-31", 2027),  # перенос 31 декабря, а 2027 нет
        (CAL, "2028-12-30", 2029),  # сб → вс 31.12 → 2029 нет
        (CAL, "2028-12-31", 2029),
        (CAL, "2025-06-02", 2025),  # года нет вовсе
    ],
)
def test_next_workday_missing_year(cal, day, missing):
    with pytest.raises(MissingYearError) as exc:
        next_workday(d(day), cal)
    assert exc.value.year == missing


# --- apply_shift ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "shift", "expected"),
    [
        ("2026-01-31", "next_workday", "2026-02-02"),
        ("2026-03-05", "next_workday", "2026-03-10"),
        ("2026-12-31", "next_workday", "2027-01-06"),
        ("2026-01-28", "next_workday", "2026-01-28"),
        ("2026-01-31", "none", "2026-01-31"),
        ("2026-03-05", "none", "2026-03-05"),
        ("2026-12-31", "none", "2026-12-31"),
    ],
)
def test_apply_shift(day, shift, expected):
    assert apply_shift(d(day), shift, CAL) == d(expected)


def test_apply_shift_none_does_not_need_calendar():
    empty = WorkdayCalendar(years={})
    assert apply_shift(d("2031-01-04"), "none", empty) == d("2031-01-04")


def test_apply_shift_unknown_value():
    with pytest.raises(ValueError):
        apply_shift(d("2026-01-28"), "previous_workday", CAL)  # type: ignore[arg-type]


# --- rule_dates: yearly, каждый месяц × число × год ------------------------------------------


@pytest.mark.parametrize(("year", "lengths"), YEARS, ids=["2026", "2028-leap"])
@pytest.mark.parametrize("day", [1, 28, 29, 30, 31])
@pytest.mark.parametrize("month", range(1, 13))
def test_rule_dates_yearly(month, day, year, lengths):
    assert rule_dates(YearlyRule(month=month, day=day), year) == [
        _expected(year, month, day, lengths)
    ]


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, "2024-02-29"),  # високосный
        (2026, "2026-02-28"),  # невисокосный → последний день февраля
        (2027, "2027-02-28"),
        (2028, "2028-02-29"),
        (2000, "2000-02-29"),  # кратен 400 — високосный
        (2100, "2100-02-28"),  # кратен 100 — не високосный
    ],
)
def test_rule_dates_feb_29(year, expected):
    assert rule_dates(YearlyRule(month=2, day=29), year) == [d(expected)]


@pytest.mark.parametrize(
    ("month", "expected"),
    [(4, "2026-04-30"), (6, "2026-06-30"), (9, "2026-09-30"), (11, "2026-11-30")],
)
def test_rule_dates_31_in_30_day_month(month, expected):
    assert rule_dates(YearlyRule(month=month, day=31), 2026) == [d(expected)]


# --- rule_dates: quarterly ---------------------------------------------------------------------

# Смещение от конца квартала → месяцы дат в календарном году (первый — за IV квартал прошлого).
QUARTERLY_MONTHS = {1: (1, 4, 7, 10), 2: (2, 5, 8, 11), 3: (3, 6, 9, 12)}


@pytest.mark.parametrize(("year", "lengths"), YEARS, ids=["2026", "2028-leap"])
@pytest.mark.parametrize("day", [1, 15, 29, 30, 31])
@pytest.mark.parametrize("offset", [1, 2, 3])
def test_rule_dates_quarterly(offset, day, year, lengths):
    expected = [_expected(year, m, day, lengths) for m in QUARTERLY_MONTHS[offset]]
    assert rule_dates(QuarterlyRule(offset_month=offset, day=day), year) == expected


def test_rule_dates_quarterly_january_is_previous_q4():
    """Январская дата года — за IV квартал прошлого года; декабрьский IV квартал — в следующем."""
    assert rule_dates(QuarterlyRule(offset_month=1, day=25), 2027) == [
        d("2027-01-25"),
        d("2027-04-25"),
        d("2027-07-25"),
        d("2027-10-25"),
    ]


# --- rule_dates: monthly_in_quarter ------------------------------------------------------------

MONTHLY_MONTHS = {
    (1,): (1, 4, 7, 10),
    (2,): (2, 5, 8, 11),
    (3,): (3, 6, 9, 12),
    (1, 2): (1, 2, 4, 5, 7, 8, 10, 11),
    (1, 3): (1, 3, 4, 6, 7, 9, 10, 12),
    (2, 3): (2, 3, 5, 6, 8, 9, 11, 12),
    (1, 2, 3): tuple(range(1, 13)),
}


@pytest.mark.parametrize(("year", "lengths"), YEARS, ids=["2026", "2028-leap"])
@pytest.mark.parametrize("day", [28, 29, 31])
@pytest.mark.parametrize("months", list(MONTHLY_MONTHS), ids=str)
def test_rule_dates_monthly_in_quarter(months, day, year, lengths):
    result = rule_dates(MonthlyInQuarterRule(months=months, day=day), year)
    assert result == [_expected(year, m, day, lengths) for m in MONTHLY_MONTHS[months]]
    assert len(result) == len(months) * 4


def test_rule_dates_monthly_in_quarter_explicit():
    """Пример из types.py: months=(1, 2, 3), day=28 — III квартал → 28 октября, ноября, декабря."""
    result = rule_dates(MonthlyInQuarterRule(months=(1, 2, 3), day=28), 2026)
    assert result[-3:] == [d("2026-10-28"), d("2026-11-28"), d("2026-12-28")]
    assert result[:3] == [d("2026-01-28"), d("2026-02-28"), d("2026-03-28")]


# --- obligation_dates: правило × месяц × перенос по тестовому календарю -----------------------

YEARLY_28_2026 = [
    ("2026-01-28", "2026-01-28"),
    ("2026-02-28", "2026-03-02"),  # сб
    ("2026-03-28", "2026-03-30"),  # сб
    ("2026-04-28", "2026-04-29"),  # праздник
    ("2026-05-28", "2026-05-28"),
    ("2026-06-28", "2026-06-29"),  # вс
    ("2026-07-28", "2026-07-28"),
    ("2026-08-28", "2026-08-28"),
    ("2026-09-28", "2026-09-28"),
    ("2026-10-28", "2026-10-28"),
    ("2026-11-28", "2026-11-29"),  # сб → рабочее воскресенье
    ("2026-12-28", "2026-12-28"),
]
YEARLY_25_2026 = [
    ("2026-01-25", "2026-01-26"),  # вс
    ("2026-02-25", "2026-02-25"),
    ("2026-03-25", "2026-03-25"),
    ("2026-04-25", "2026-04-27"),  # сб
    ("2026-05-25", "2026-05-25"),
    ("2026-06-25", "2026-06-25"),
    ("2026-07-25", "2026-07-27"),  # сб
    ("2026-08-25", "2026-08-25"),
    ("2026-09-25", "2026-09-25"),
    ("2026-10-25", "2026-10-27"),  # вс + праздник в пн
    ("2026-11-25", "2026-11-25"),
    ("2026-12-25", "2026-12-25"),
]
YEARLY_31_2026 = [
    ("2026-01-31", "2026-02-02"),  # сб
    ("2026-02-28", "2026-03-02"),  # 31 → 28 февраля, сб
    ("2026-03-31", "2026-03-31"),
    ("2026-04-30", "2026-04-30"),  # 31 → 30 апреля
    ("2026-05-31", "2026-06-01"),  # вс
    ("2026-06-30", "2026-06-30"),
    ("2026-07-31", "2026-07-31"),
    ("2026-08-31", "2026-08-31"),
    ("2026-09-30", "2026-09-30"),
    ("2026-10-31", "2026-11-02"),  # сб
    ("2026-11-30", "2026-11-30"),
    ("2026-12-31", "2027-01-06"),  # праздник → январь следующего года
]


@pytest.mark.parametrize(
    ("day", "original", "due"),
    [(28, *p) for p in YEARLY_28_2026]
    + [(25, *p) for p in YEARLY_25_2026]
    + [(31, *p) for p in YEARLY_31_2026],
)
def test_obligation_dates_yearly(day, original, due):
    month = d(original).month
    ob = _obligation(YearlyRule(month=month, day=day))
    assert obligation_dates(ob, 2026, CAL) == [DueDate(d(original), d(due))]


@pytest.mark.parametrize(
    ("rule", "year", "expected"),
    [
        (
            QuarterlyRule(offset_month=1, day=25),
            2026,
            [
                ("2026-01-25", "2026-01-26"),
                ("2026-04-25", "2026-04-27"),
                ("2026-07-25", "2026-07-27"),
                ("2026-10-25", "2026-10-27"),
            ],
        ),
        (
            QuarterlyRule(offset_month=2, day=28),
            2026,
            [
                ("2026-02-28", "2026-03-02"),
                ("2026-05-28", "2026-05-28"),
                ("2026-08-28", "2026-08-28"),
                ("2026-11-28", "2026-11-29"),
            ],
        ),
        (
            QuarterlyRule(offset_month=3, day=31),
            2026,
            [
                ("2026-03-31", "2026-03-31"),
                ("2026-06-30", "2026-06-30"),
                ("2026-09-30", "2026-09-30"),
                ("2026-12-31", "2027-01-06"),
            ],
        ),
        (
            QuarterlyRule(offset_month=1, day=25),
            2027,
            [
                ("2027-01-25", "2027-01-25"),  # за IV квартал 2026
                ("2027-04-25", "2027-04-27"),  # вс + праздник в пн
                ("2027-07-25", "2027-07-26"),
                ("2027-10-25", "2027-10-25"),
            ],
        ),
        (
            QuarterlyRule(offset_month=1, day=5),
            2027,
            [
                ("2027-01-05", "2027-01-06"),  # январская дата IV квартала — на праздник
                ("2027-04-05", "2027-04-05"),
                ("2027-07-05", "2027-07-05"),
                ("2027-10-05", "2027-10-05"),
            ],
        ),
        (
            MonthlyInQuarterRule(months=(1, 2, 3), day=28),
            2026,
            YEARLY_28_2026,
        ),
        (
            MonthlyInQuarterRule(months=(2,), day=29),
            2028,
            [
                ("2028-02-29", "2028-03-01"),  # 29 февраля високосного — праздник
                ("2028-05-29", "2028-05-29"),
                ("2028-08-29", "2028-08-29"),
                ("2028-11-29", "2028-11-29"),
            ],
        ),
        (
            MonthlyInQuarterRule(months=(2,), day=29),
            2027,
            [
                ("2027-02-28", "2027-03-01"),  # невисокосный: 28 февраля, вс
                ("2027-05-29", "2027-05-31"),
                ("2027-08-29", "2027-08-30"),
                ("2027-11-29", "2027-11-29"),
            ],
        ),
        (YearlyRule(month=2, day=29), 2026, [("2026-02-28", "2026-03-02")]),
        (YearlyRule(month=2, day=29), 2027, [("2027-02-28", "2027-03-01")]),
        (YearlyRule(month=2, day=29), 2028, [("2028-02-29", "2028-03-01")]),
    ],
    ids=lambda v: str(v) if isinstance(v, int) else None,
)
def test_obligation_dates_rules(rule, year, expected):
    result = obligation_dates(_obligation(rule), year, CAL)
    assert result == [DueDate(d(o), d(due)) for o, due in expected]


def test_obligation_dates_shift_none():
    ob = _obligation(QuarterlyRule(offset_month=3, day=31), shift="none")
    assert obligation_dates(ob, 2026, CAL) == [
        DueDate(d("2026-03-31"), d("2026-03-31")),
        DueDate(d("2026-06-30"), d("2026-06-30")),
        DueDate(d("2026-09-30"), d("2026-09-30")),
        DueDate(d("2026-12-31"), d("2026-12-31")),  # праздник, но переноса нет
    ]


def test_obligation_dates_shift_none_without_calendar_year():
    ob = _obligation(YearlyRule(month=1, day=4), shift="none")
    assert obligation_dates(ob, 2031, WorkdayCalendar(years={})) == [
        DueDate(d("2031-01-04"), d("2031-01-04"))
    ]


@pytest.mark.parametrize(
    ("cal", "rule", "year", "missing"),
    [
        (CAL, YearlyRule(month=3, day=2), 2029, 2029),  # года нет
        (CAL, YearlyRule(month=3, day=2), 2025, 2025),
        (CAL_ONLY_2026, YearlyRule(month=12, day=31), 2026, 2027),  # перенос в отсутствующий
        (CAL_ONLY_2026, QuarterlyRule(offset_month=3, day=31), 2026, 2027),
        (CAL, YearlyRule(month=12, day=31), 2028, 2029),  # 31.12.2028 — вс
    ],
)
def test_obligation_dates_missing_year(cal, rule, year, missing):
    with pytest.raises(MissingYearError) as exc:
        obligation_dates(_obligation(rule), year, cal)
    assert exc.value.year == missing


def test_obligation_dates_original_in_year_due_may_leave_it():
    result = obligation_dates(_obligation(YearlyRule(month=12, day=31)), 2026, CAL)
    assert [r.original_date.year for r in result] == [2026]
    assert [r.due_date.year for r in result] == [2027]


# --- НДС ---------------------------------------------------------------------------------------

# ТЕСТОВЫЕ ДАННЫЕ: пороги для проверки логики, не содержимое nds.yaml.
LIMIT_20M = 20_000_000
LIMIT_15M = 15_000_000
LIMIT_10M = 10_000_000


def _nds(*thresholds: tuple[tuple[int, ...], int]) -> NdsConfig:
    return NdsConfig(
        law="Тестовый закон",
        law_url="https://example.invalid/law",
        fns_guide_url="https://example.invalid/fns",
        thresholds=tuple(NdsThreshold(income_years=y, limit_rub=lim) for y, lim in thresholds),
        last_checked_at=date(2030, 1, 1),
    )


NDS = _nds(
    ((2025, 2026, 2027, 2028), LIMIT_20M),
    ((2029,), LIMIT_15M),
    ((2030,), LIMIT_10M),
)


@pytest.mark.parametrize(
    ("income_year", "expected"),
    [
        (2025, LIMIT_20M),
        (2026, LIMIT_20M),
        (2028, LIMIT_20M),
        (2029, LIMIT_15M),
        (2030, LIMIT_10M),
        (2024, None),
        (2031, None),
    ],
)
def test_nds_limit_for(income_year, expected):
    assert nds_limit_for(income_year, NDS) == expected


def test_nds_limit_for_first_match_wins():
    nds = _nds(((2029,), LIMIT_15M), ((2029, 2030), LIMIT_10M))
    assert nds_limit_for(2029, nds) == LIMIT_15M
    assert nds_limit_for(2030, nds) == LIMIT_10M


def test_nds_limit_for_empty_thresholds():
    assert nds_limit_for(2026, _nds()) is None


BANDS = ["lt10", "10_20", "20_60", "gt60", "unknown", None]
NDS_PAYER_TABLE = {
    # год дохода: ожидание по диапазонам в порядке BANDS
    2025: [False, False, True, True, None, None],  # порог 20 млн
    2028: [False, False, True, True, None, None],  # порог 20 млн
    2029: [False, None, True, True, None, None],  # порог 15 млн — 10_20 пересекает
    2030: [False, True, True, True, None, None],  # порог 10 млн
    2031: [None, None, None, None, None, None],  # порога нет
}


@pytest.mark.parametrize(
    ("income_year", "band", "expected"),
    [(y, b, e) for y, row in NDS_PAYER_TABLE.items() for b, e in zip(BANDS, row, strict=True)],
)
def test_nds_payer(income_year, band, expected):
    assert nds_payer(band, income_year, NDS) is expected


# Порог ровно на границе кнопок и на рубль около неё. Числа границ берутся из types — не дублируем.
_LT10_UPPER = INCOME_BAND_BOUNDS_RUB["lt10"][1]
_GT60_LOWER = INCOME_BAND_BOUNDS_RUB["gt60"][0]


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (_LT10_UPPER - 1, {"lt10": None, "10_20": True, "20_60": True, "gt60": True}),
        (_LT10_UPPER, {"lt10": False, "10_20": True, "20_60": True, "gt60": True}),
        (_LT10_UPPER + 1, {"lt10": False, "10_20": None, "20_60": True, "gt60": True}),
        (_GT60_LOWER - 1, {"lt10": False, "10_20": False, "20_60": None, "gt60": True}),
        (_GT60_LOWER, {"lt10": False, "10_20": False, "20_60": False, "gt60": True}),
        (_GT60_LOWER + 1, {"lt10": False, "10_20": False, "20_60": False, "gt60": None}),
    ],
)
def test_nds_payer_limit_at_band_bounds(limit, expected):
    nds = _nds(((2026,), limit))
    assert {band: nds_payer(band, 2026, nds) for band in expected} == expected


@pytest.mark.parametrize("band", ["", "foo", "10-20", "LT10"])
def test_nds_payer_unknown_band_value(band):
    with pytest.raises(ValueError):
        nds_payer(band, 2026, NDS)


# --- Чистота модуля -----------------------------------------------------------------------------


def test_dates_module_is_pure():
    """Без текущего времени, БД и сети; налоговых дат и порогов в коде нет."""
    tree = ast.parse(inspect.getsource(dates))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & {"today", "now", "utcnow"}
    imported = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert all(not str(m).startswith(("app.core", "sqlalchemy", "httpx")) for m in imported)
    numbers = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) is int
    }
    assert all(n <= 12 for n in numbers), numbers
