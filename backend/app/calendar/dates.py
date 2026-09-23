"""Движок дат (задача T3). Сейчас — только контракт.

Все функции чистые: без БД, сети и текущего времени — всё нужное приходит аргументами.
Производственный календарь — только из WorkdayCalendar (workdays.yaml), не по памяти.
Смысл правил и обработка несуществующего числа месяца — в docstring-ах app/calendar/types.py.
"""

from datetime import date

from app.calendar.types import (
    DateRule,
    DueDate,
    NdsConfig,
    Obligation,
    Shift,
    WorkdayCalendar,
)


def is_workday(day: date, cal: WorkdayCalendar) -> bool:
    """Рабочий ли день: пн–пт, кроме `holidays`, плюс перенесённые `workdays`.

    Года нет в календаре — MissingYearError.
    """
    raise NotImplementedError("T3")


def next_workday(day: date, cal: WorkdayCalendar) -> date:
    """Сам `day`, если он рабочий, иначе ближайший следующий рабочий день.

    Перенос может перейти в следующий год (31 декабря → январь); нужного года нет —
    MissingYearError.
    """
    raise NotImplementedError("T3")


def apply_shift(day: date, shift: Shift, cal: WorkdayCalendar) -> date:
    """`next_workday` → next_workday(day, cal); `none` → day без изменений (календарь не нужен)."""
    raise NotImplementedError("T3")


def rule_dates(rule: DateRule, year: int) -> list[date]:
    """Все даты правила (до переноса), попадающие в календарный год `year`, по возрастанию.

    yearly — одна дата; quarterly — четыре (январская относится к IV кварталу прошлого года);
    monthly_in_quarter — len(months) × 4 даты, из них попадающие в `year`.
    """
    raise NotImplementedError("T3")


def obligation_dates(obligation: Obligation, year: int, cal: WorkdayCalendar) -> list[DueDate]:
    """Даты обязательства, у которых `original_date` в году `year`, с переносом по `shift`.

    `due_date` после переноса может оказаться в следующем году — это нормально.
    """
    raise NotImplementedError("T3")


def nds_limit_for(income_year: int, nds: NdsConfig) -> int | None:
    """Порог (руб.) для дохода за `income_year`; года нет ни в одном `income_years` — None."""
    raise NotImplementedError("T3")


def nds_payer(income_band: str | None, income_year: int, nds: NdsConfig) -> bool | None:
    """Плательщик ли НДС по диапазону дохода за `income_year` (экран 3, D14, D18).

    Диапазон — types.INCOME_BAND_BOUNDS_RUB, порог — nds_limit_for(income_year, nds):
    - верхняя граница диапазона не больше порога → False;
    - нижняя граница не меньше порога → True;
    - диапазон пересекает порог (10_20 при пороге 15 млн), `unknown`, None
      или порога на этот год нет → None: определить нельзя.
    Режим (patent, ausn) здесь не учитывается — это решают тексты экрана 3.
    """
    raise NotImplementedError("T3")
