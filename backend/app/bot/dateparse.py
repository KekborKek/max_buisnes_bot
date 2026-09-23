"""Разбор даты и названия задачи из свободного текста сообщения (экран 9, экран 10).

Правилами, без языковой модели и внешних зависимостей. Функция чистая: не читает БД, сеть
и текущее время — «сегодня» передаётся аргументом.

Поддерживаемые форматы (таблица экрана 9):
    - число + месяц словом: «5 ноября», «5 нояб» (год не указан → ближайшее не прошедшее число,
      при необходимости следующий год);
    - ДД.ММ(.ГГГГ): «05.11», «5.11.2026» (год указан явно → дата возвращается как есть, даже если
      она уже в прошлом; экран 9 показывает `past_date` — это решает обработчик T8b, не парсер);
    - относительный день: «сегодня», «завтра», «послезавтра»;
    - день недели: «в понедельник», «во вторник», «пн» и т.п. — ближайший будущий такой день,
      сегодняшний день недели не считается (минимум +7 дней).

Допущения (явно не описаны в экранах 9/10, отмечены в PR):
    - `explicit_year` истинен только когда год в тексте указан явно (формат ДД.ММ.ГГГГ) — по этому
      флагу обработчик решает, показывать ли `past_date` (для форматов без года дата никогда не
      уходит в прошлое, парсер сам подбирает ближайший не прошедший год).
    - Если после вычитания даты (и предлога перед ней) от текста ничего не остаётся (сообщение —
      это только дата, например «5 ноября»), `title` — пустая строка; статус всё равно `PARSED`,
      что с этим делать дальше — решает обработчик T8b.
    - Несуществующая календарная дата («31.02», «32 ноября», «30 февраля») — это тоже «непохоже
      на дату», но текст явно про дату, поэтому результат `NO_DATE`, а не `UNKNOWN`
      (экран 10: «не нашёл дату», а не «не понял»).
    - «Не понял» (`UNKNOWN`) против «не нашёл дату» (`NO_DATE`) отличаем эвристикой экрана 10:
      просто похоже на задачу (два и более слова, либо слово похоже на глагол по окончанию
      -ть/-чь/-ти) → `NO_DATE`; иначе → `UNKNOWN`.
    - Числа вида «1.5 млн» синтаксически похожи на ДД.ММ (1.05 — валидная дата), но сразу за ними
      идёт слово-маркер суммы/количества (`млн`, `тыс`, `руб`, `%`, …) — такие совпадения не
      считаются датой, текст остаётся как есть и уходит в общую эвристику NO_DATE/UNKNOWN.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class ParseStatus(Enum):
    """Три исхода разбора — ровно те, что различает экран 10."""

    PARSED = "parsed"  # дата и название распознаны
    NO_DATE = "no_date"  # похоже на задачу (или на попытку указать дату), даты нет
    UNKNOWN = "unknown"  # ни даты, ни похожего на задачу текста


@dataclass(frozen=True)
class ParsedTask:
    """Результат разбора одного сообщения."""

    status: ParseStatus
    title: str | None = None  # для PARSED и NO_DATE
    due_date: date | None = None  # только для PARSED
    explicit_year: bool = False  # True — год в тексте указан явно (может быть в прошлом)


_TITLE_MAX_LEN = 60

_MONTH_FORMS: dict[int, tuple[str, ...]] = {
    1: ("январь", "января"),
    2: ("февраль", "февраля"),
    3: ("март", "марта"),
    4: ("апрель", "апреля"),
    5: ("май", "мая"),
    6: ("июнь", "июня"),
    7: ("июль", "июля"),
    8: ("август", "августа"),
    9: ("сентябрь", "сентября"),
    10: ("октябрь", "октября"),
    11: ("ноябрь", "ноября"),
    12: ("декабрь", "декабря"),
}

_WEEKDAY_FORMS: dict[int, tuple[str, ...]] = {
    0: ("понедельник", "пн"),
    1: ("вторник", "вт"),
    2: ("среда", "среду", "ср"),
    3: ("четверг", "чт"),
    4: ("пятница", "пятницу", "пт"),
    5: ("суббота", "субботу", "сб"),
    6: ("воскресенье", "вс"),
}
_WEEKDAY_BY_FORM: dict[str, int] = {
    form: idx for idx, forms in _WEEKDAY_FORMS.items() for form in forms
}
_WEEKDAY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_WEEKDAY_BY_FORM, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_RELATIVE_OFFSETS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_RELATIVE_PATTERN = re.compile(r"\b(сегодня|завтра|послезавтра)\b", re.IGNORECASE)

_NUMERIC_DATE_PATTERN = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b")
_MONTH_WORD_PATTERN = re.compile(r"\b(\d{1,2})\s+([а-яёА-ЯЁ]+\.?)\b")

# Слова-маркеры сумм/количеств: «1.5 млн» синтаксически похоже на ДД.ММ, но датой не является.
_STOP_WORDS_AFTER = {
    "млн",
    "млрд",
    "тыс",
    "руб",
    "рубль",
    "рублей",
    "рубля",
    "рублях",
    "%",
    "процент",
    "процента",
    "процентов",
    "га",
    "кг",
    "шт",
    "штук",
    "человек",
}

# Предлоги/фразы, которые вместе с датой убираются из {title}.
_PREPOSITIONS = ("не позднее", "до", "к", "ко", "на", "во", "в")
_PREPOSITION_PATTERN = re.compile(
    r"(?:^|\s)("
    + "|".join(re.escape(p) for p in sorted(_PREPOSITIONS, key=len, reverse=True))
    + r")\s*$",
    re.IGNORECASE,
)

_VERB_SUFFIXES = ("ть", "чь", "ти")


def parse_task_from_text(text: str, today: date) -> ParsedTask:
    """Разбирает свободный текст сообщения на дату и название задачи.

    `today` — «сегодня» пользователя, передаётся аргументом (не `date.today()`), чтобы функция
    оставалась чистой и её можно было тестировать на любой день.
    """
    if not text or not text.strip():
        return ParsedTask(status=ParseStatus.UNKNOWN)

    finds = (
        _find_numeric_date(text, today),
        _find_month_word_date(text, today),
        _find_weekday(text, today),
        _find_relative(text, today),
    )

    valid = [f for f in finds if f is not None and f[0] == "valid"]
    if valid:
        _, start, end, due_date, explicit_year = min(valid, key=lambda f: f[1])
        title = _build_title(text, start, end)
        return ParsedTask(
            status=ParseStatus.PARSED,
            title=title,
            due_date=due_date,
            explicit_year=explicit_year,
        )

    invalid = [f for f in finds if f is not None and f[0] == "invalid"]
    if invalid:
        _, start, end = min(invalid, key=lambda f: f[1])
        title = _build_title(text, start, end)
        return ParsedTask(status=ParseStatus.NO_DATE, title=title)

    return _fallback(text)


def _roll_forward(today: date, month: int, day: int) -> date | None:
    """Ближайшая не прошедшая дата с этим числом и месяцем: этот год, иначе следующий.

    `None` — число/месяц не образуют существующую календарную дату ни в этом, ни в следующем
    году (например, 30 февраля, 32-е число).
    """
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def _find_numeric_date(
    text: str, today: date
) -> tuple[str, int, int, date, bool] | tuple[str, int, int] | None:
    for m in _NUMERIC_DATE_PATTERN.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12):
            continue
        tail = text[m.end() :].lstrip()
        tail_word = re.match(r"[а-яёА-ЯЁ%]+", tail)
        if tail_word and tail_word.group(0).lower() in _STOP_WORDS_AFTER:
            continue  # похоже на сумму/количество («1.5 млн»), не на дату
        year_group = m.group(3)
        if year_group:
            try:
                due_date = date(int(year_group), month, day)
            except ValueError:
                return ("invalid", m.start(), m.end())
            return ("valid", m.start(), m.end(), due_date, True)
        due_date = _roll_forward(today, month, day)
        if due_date is None:
            return ("invalid", m.start(), m.end())
        return ("valid", m.start(), m.end(), due_date, False)
    return None


def _match_month(token: str) -> int | None:
    normalized = token.lower().rstrip(".")
    if len(normalized) < 3:
        return None
    for month_num, forms in _MONTH_FORMS.items():
        if any(form.startswith(normalized) for form in forms):
            return month_num
    return None


def _find_month_word_date(
    text: str, today: date
) -> tuple[str, int, int, date, bool] | tuple[str, int, int] | None:
    for m in _MONTH_WORD_PATTERN.finditer(text):
        month = _match_month(m.group(2))
        if month is None:
            continue
        day = int(m.group(1))
        due_date = _roll_forward(today, month, day)
        if due_date is None:
            return ("invalid", m.start(), m.end())
        return ("valid", m.start(), m.end(), due_date, False)
    return None


def _find_weekday(text: str, today: date) -> tuple[str, int, int, date, bool] | None:
    m = _WEEKDAY_PATTERN.search(text)
    if not m:
        return None
    weekday = _WEEKDAY_BY_FORM[m.group(1).lower()]
    days_ahead = (weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # сегодняшний день недели не считается
    due_date = today + timedelta(days=days_ahead)
    return ("valid", m.start(), m.end(), due_date, False)


def _find_relative(text: str, today: date) -> tuple[str, int, int, date, bool] | None:
    m = _RELATIVE_PATTERN.search(text)
    if not m:
        return None
    offset = _RELATIVE_OFFSETS[m.group(1).lower()]
    due_date = today + timedelta(days=offset)
    return ("valid", m.start(), m.end(), due_date, False)


def _build_title(text: str, start: int, end: int) -> str:
    """`{title}`: текст без даты (и предлога перед ней), с заглавной буквы, до 60 символов."""
    prefix = text[:start]
    prep_match = _PREPOSITION_PATTERN.search(prefix)
    strip_start = prep_match.start(1) if prep_match else start
    remainder = text[:strip_start] + text[end:]
    remainder = re.sub(r"\s+", " ", remainder).strip(" ,.;:!?—-")
    if not remainder:
        return ""
    remainder = remainder[0].upper() + remainder[1:]
    return remainder[:_TITLE_MAX_LEN].rstrip()


def _looks_like_verb(word: str) -> bool:
    cleaned = word.strip(string.punctuation).lower()
    return len(cleaned) >= 3 and cleaned.endswith(_VERB_SUFFIXES)


def _fallback(text: str) -> ParsedTask:
    """Ни один формат даты не распознан. Отличаем «не нашёл дату» от «не понял» (экран 10)."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ParsedTask(status=ParseStatus.UNKNOWN)
    words = normalized.split(" ")
    looks_like_task = len(words) >= 2 or _looks_like_verb(words[0])
    if not looks_like_task:
        return ParsedTask(status=ParseStatus.UNKNOWN)
    title = normalized[0].upper() + normalized[1:]
    return ParsedTask(status=ParseStatus.NO_DATE, title=title[:_TITLE_MAX_LEN].rstrip())
