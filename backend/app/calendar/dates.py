"""Движок дат (задача T3): правило → дата, перенос на рабочий день, nds_payer.

Все функции чистые: без БД, сети и текущего времени — всё нужное приходит аргументами.
Производственный календарь — только из WorkdayCalendar (workdays.yaml), не по памяти.
Смысл правил и обработка несуществующего числа месяца — в docstring-ах app/calendar/types.py.
"""

from calendar import monthrange
from datetime import date, timedelta

from app.calendar.types import (
    INCOME_BAND_BOUNDS_RUB,
    INCOME_BANDS,
    DateRule,
    DueDate,
    MissingYearError,
    MonthlyInQuarterRule,
    NdsConfig,
    Obligation,
    QuarterlyRule,
    Shift,
    WorkdayCalendar,
    YearlyRule,
)

_ONE_DAY = timedelta(days=1)
_SATURDAY = 5  # date.weekday(): пн = 0 … вс = 6


def _clamped(year: int, month: int, day: int) -> date:
    """`day` число месяца; такого числа нет — последний день месяца (types.py, НК РФ ст. 6.1)."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def _months_after_quarters(offsets: tuple[int, ...], year: int) -> list[tuple[int, int]]:
    """(год, месяц) для каждого квартала и каждого смещения от его последнего месяца,
    попадающие в календарный год `year`. IV квартал прошлого года тоже учитывается."""
    quarter_ends = [(year - 1, 12), (year, 3), (year, 6), (year, 9), (year, 12)]
    result = []
    for end_year, end_month in quarter_ends:
        for offset in offsets:
            months_total = end_year * 12 + (end_month - 1) + offset
            target_year, target_month = divmod(months_total, 12)
            if target_year == year:
                result.append((target_year, target_month + 1))
    return result


def is_workday(day: date, cal: WorkdayCalendar) -> bool:
    """Рабочий ли день: пн–пт, кроме `holidays`, плюс перенесённые `workdays`.

    Года нет в календаре — MissingYearError.
    """
    year_days = cal.years.get(day.year)
    if year_days is None:
        raise MissingYearError(day.year)
    if day in year_days.workdays:
        return True
    if day in year_days.holidays:
        return False
    return day.weekday() < _SATURDAY


def next_workday(day: date, cal: WorkdayCalendar) -> date:
    """Сам `day`, если он рабочий, иначе ближайший следующий рабочий день.

    Перенос может перейти в следующий год (31 декабря → январь); нужного года нет —
    MissingYearError.
    """
    while not is_workday(day, cal):
        day += _ONE_DAY
    return day


def apply_shift(day: date, shift: Shift, cal: WorkdayCalendar) -> date:
    """`next_workday` → next_workday(day, cal); `none` → day без изменений (календарь не нужен)."""
    if shift == "next_workday":
        return next_workday(day, cal)
    if shift == "none":
        return day
    raise ValueError(f"Неизвестный shift: {shift!r}")


def rule_dates(rule: DateRule, year: int) -> list[date]:
    """Все даты правила (до переноса), попадающие в календарный год `year`, по возрастанию.

    yearly — одна дата; quarterly — четыре (январская относится к IV кварталу прошлого года);
    monthly_in_quarter — len(months) × 4 даты, из них попадающие в `year`.
    """
    if isinstance(rule, YearlyRule):
        return [_clamped(year, rule.month, rule.day)]
    if isinstance(rule, QuarterlyRule):
        offsets: tuple[int, ...] = (rule.offset_month,)
    elif isinstance(rule, MonthlyInQuarterRule):
        offsets = rule.months
    else:
        raise TypeError(f"Неизвестное правило даты: {rule!r}")
    return sorted(_clamped(y, m, rule.day) for y, m in _months_after_quarters(offsets, year))


def obligation_dates(obligation: Obligation, year: int, cal: WorkdayCalendar) -> list[DueDate]:
    """Даты обязательства, у которых `original_date` в году `year`, с переносом по `shift`.

    `due_date` после переноса может оказаться в следующем году — это нормально.
    """
    return [
        DueDate(original_date=original, due_date=apply_shift(original, obligation.shift, cal))
        for original in rule_dates(obligation.date_rule, year)
    ]


def nds_limit_for(income_year: int, nds: NdsConfig) -> int | None:
    """Порог (руб.) для дохода за `income_year`.

    Год встречается в `income_years` одного из порогов — берём его лимит. Год позже
    последнего в справочнике (#106: закон называет годы только по 2030-й, дальше порог
    не меняется по логике нормы) — берём лимит последней записи. Года раньше первой
    записи справочника нет — None: данных для него действительно нет.
    """
    for threshold in nds.thresholds:
        if income_year in threshold.income_years:
            return threshold.limit_rub
    if not nds.thresholds:
        return None
    latest = max(nds.thresholds, key=lambda th: max(th.income_years))
    if income_year > max(latest.income_years):
        return latest.limit_rub
    return None


def nds_payer(income_band: str | None, income_year: int, nds: NdsConfig) -> bool | None:
    """Плательщик ли НДС по диапазону дохода за `income_year` (экран 3, D14, D18, D25).

    Диапазон — types.INCOME_BAND_BOUNDS_RUB, порог — nds_limit_for(income_year, nds):
    - верхняя граница диапазона не больше порога → False;
    - нижняя граница не меньше порога → True;
    - диапазон пересекает порог (10_20 при пороге 15 млн), `unknown`, None
      или порога на этот год нет → None: определить нельзя.
    Режим (patent, ausn) здесь не учитывается — это решают тексты экрана 3.
    """
    if income_band is not None and income_band not in INCOME_BANDS:
        raise ValueError(f"Неизвестный диапазон дохода: {income_band!r}")
    if income_band is None or income_band == "unknown":
        return None
    limit = nds_limit_for(income_year, nds)
    if limit is None:
        return None
    lower, upper = INCOME_BAND_BOUNDS_RUB[income_band]
    if upper is not None and upper <= limit:
        return False
    if lower >= limit:
        return True
    return None
