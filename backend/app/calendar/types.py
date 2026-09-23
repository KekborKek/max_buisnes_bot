"""Контракты календаря: неизменяемые датаклассы и исключения (docs/spec/data-model.md §1).

По этим типам параллельно пишутся загрузчик (T2) и движок дат (T3). Меняет их только
техлид: правка здесь ломает обе дорожки. Логики в модуле нет — только данные.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

# --- Словари значений --------------------------------------------------------------------

Category = Literal["taxes", "contributions", "reports"]
Shift = Literal["next_workday", "none"]
IncomeBand = Literal["lt10", "10_20", "20_60", "gt60", "unknown"]
Regime = Literal["usn6", "usn15", "patent", "ausn", "unknown"]
ItemType = Literal["obligation", "task"]
NotificationKind = Literal["d30", "d7", "d1", "overdue", "snooze", "task"]
NotificationStatus = Literal["pending", "sent", "cancelled", "failed"]
ItemStatus = Literal["done", "overdue", "today", "upcoming"]

CATEGORIES: frozenset[str] = frozenset({"taxes", "contributions", "reports"})
SHIFTS: frozenset[str] = frozenset({"next_workday", "none"})
INCOME_BANDS: frozenset[str] = frozenset({"lt10", "10_20", "20_60", "gt60", "unknown"})
REGIMES: frozenset[str] = frozenset({"usn6", "usn15", "patent", "ausn", "unknown"})

# Границы ответов на вопрос 1 онбординга (экран 2) в рублях: доход БОЛЬШЕ нижней границы
# и НЕ БОЛЬШЕ верхней, None — границы нет. Это определения кнопок онбординга, а не налоговые
# данные (D25); порог НДС берётся только из nds.yaml. `unknown` границ не имеет.
# Единственное место с этими числами — не дублировать в боте, API и тестах.
INCOME_BAND_BOUNDS_RUB: dict[str, tuple[int, int | None]] = {
    "lt10": (0, 10_000_000),
    "10_20": (10_000_000, 20_000_000),
    "20_60": (20_000_000, 60_000_000),
    "gt60": (60_000_000, None),
}


# --- Правила дат (data-model.md §1, «Правила дат») ------------------------------------------
# Если в месяце нет нужного числа (31 апреля, 29 февраля в невисокосный год), дата — последний
# день этого месяца (по аналогии с НК РФ ст. 6.1 п. 5). Загрузчик пропускает запись только
# с числом вне 1–31 или месяцем вне 1–12.


@dataclass(frozen=True, slots=True)
class YearlyRule:
    """Раз в год: `day` число месяца `month`. YAML: {type: yearly, month, day}."""

    month: int  # 1–12
    day: int  # 1–31
    type: Literal["yearly"] = "yearly"


@dataclass(frozen=True, slots=True)
class QuarterlyRule:
    """Раз в квартал, за каждый из четырёх кварталов.

    Дата — `day` число месяца, который `offset_month`-й по счёту после последнего месяца
    квартала: offset_month=1 → январь, апрель, июль, октябрь. За IV квартал дата приходится
    на следующий календарный год. YAML: {type: quarterly, offset_month, day}.
    """

    offset_month: int  # 1–3
    day: int  # 1–31
    type: Literal["quarterly"] = "quarterly"


@dataclass(frozen=True, slots=True)
class MonthlyInQuarterRule:
    """Несколько дат за каждый квартал: `day` число каждого из месяцев `months` после квартала.

    months=(1, 2, 3) за III квартал → 28 октября, 28 ноября, 28 декабря (при day=28).
    YAML: {type: monthly_in_quarter, months: [1, 2, 3], day}.
    """

    months: tuple[int, ...]  # непустой, по возрастанию, без повторов, каждый 1–3
    day: int  # 1–31
    type: Literal["monthly_in_quarter"] = "monthly_in_quarter"


DateRule = YearlyRule | QuarterlyRule | MonthlyInQuarterRule


# --- Запись справочника ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppliesIf:
    """Условия попадания записи в календарь: все через И, None — любое значение.

    YAML: `applies_if: {}` — всем. Списки — `regime: [usn6, usn15]`,
    `income_band: [20_60, gt60]`; флаги — `has_employees: false`, `nds_payer: true`.
    Неизвестный ключ или значение вне словаря — запись невалидна (T2).
    Как сравнивать с профилем, где ответ ещё None, решает `build.applies` (T4); для
    `nds_payer: true` при nds_payer профиля None запись не попадает (D25).
    """

    regime: frozenset[str] | None = None
    income_band: frozenset[str] | None = None
    has_employees: bool | None = None
    nds_payer: bool | None = None


@dataclass(frozen=True, slots=True)
class HowtoLink:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class Obligation:
    """Одна запись content/obligations.yaml. Все поля обязательны (data-model.md §1).

    `howto_steps` — ровно три строки; могут содержать подстановки вида `{due_date}`,
    `{notice_date}` — их заполняет тот, кто показывает шаги, загрузчик оставляет как есть.
    """

    id: str
    title: str
    category: Category
    date_rule: DateRule
    shift: Shift
    applies_if: AppliesIf
    needs_prep: bool
    norm: str
    source_url: str
    howto_steps: tuple[str, str, str]
    howto_link: HowtoLink
    penalty_text: str
    rule_version: int
    last_checked_at: date


@dataclass(frozen=True, slots=True)
class Catalog:
    """Справочник целиком: только валидные записи, id уникальны."""

    version: str  # `version` из файла — дата последней правки справочника
    obligations: tuple[Obligation, ...]


# --- Производственный календарь (content/workdays.yaml) -------------------------------------


@dataclass(frozen=True, slots=True)
class YearWorkdays:
    """Отличия года от «суббота и воскресенье — выходные»."""

    holidays: frozenset[date]  # нерабочие дни сверх суббот и воскресений
    workdays: frozenset[date]  # перенесённые рабочие субботы и воскресенья


@dataclass(frozen=True, slots=True)
class WorkdayCalendar:
    """Года, которых нет в словаре, неизвестны: обращение к ним — MissingYearError."""

    years: dict[int, YearWorkdays]


# --- Конфиг НДС (content/nds.yaml) ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NdsThreshold:
    """Доход за любой из `income_years` сравнивается с `limit_rub` (порог следующего года)."""

    income_years: tuple[int, ...]
    limit_rub: int


@dataclass(frozen=True, slots=True)
class NdsConfig:
    law: str
    law_url: str
    fns_guide_url: str
    thresholds: tuple[NdsThreshold, ...]
    last_checked_at: date


# --- Результаты и связки -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DueDate:
    """Одна дата обязательства: по правилу и после переноса на рабочий день."""

    original_date: date
    due_date: date


@dataclass(frozen=True, slots=True)
class Reference:
    """Все три справочника вместе — то, что получают сборка и планировщик."""

    catalog: Catalog
    workdays: WorkdayCalendar
    nds: NdsConfig


# --- Исключения ---------------------------------------------------------------------------------


class CalendarError(Exception):
    """Базовая ошибка календаря."""


class ReferenceFileError(CalendarError):
    """Файл справочника отсутствует, не парсится или не того формата целиком."""


class InvalidRecordError(CalendarError):
    """Одна запись справочника невалидна. Загрузчик пишет её в лог и пропускает."""

    def __init__(self, record_id: str | None, reason: str) -> None:
        self.record_id = record_id
        self.reason = reason
        super().__init__(f"{record_id or '<без id>'}: {reason}")


class MissingYearError(CalendarError):
    """Года нет в workdays.yaml: сборка на этот год невозможна (понятная ошибка в лог)."""

    def __init__(self, year: int) -> None:
        self.year = year
        super().__init__(f"В производственном календаре нет {year} года")
