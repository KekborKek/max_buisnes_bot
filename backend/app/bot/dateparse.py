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

Время (#103) разбирается вместе с датой и вырезается из названия (`remind_time`):
    - ЧЧ:ММ: «15:30», «в 15:30», «с 15:30»; часы 0–23, минуты 0–59;
    - час с суффиксом: «в 15 ч», «в 3 часа» (только с «в»), «в 9 утра», «в 3 дня», «в 7 вечера»,
      «в 12 ночи» (утра 1–11; дня 12 и 1–5 → 12, 13–17; вечера 4–11 → 16–23; ночи 12 → 0:00,
      1–4); суффикс работает и после ЧЧ:ММ: «в 7:30 вечера» — 19:30;
    - голое «в N» — время, только если за числом конец текста, знак препинания или сразу
      найденная дата («в 15 завтра маме»); иначе («в 2 этапа», «в 11 кабинете», «в 15 минут»)
      нужен `:ММ` или суффикс.
    Часы 1–6 без «утра»/«ночи» — дневные: «в 3» — 15:00, «3:30» — 15:30; с ведущим нулём —
    как написано («03:00» — 3:00).
    «до/к ЧЧ:ММ», «до/к N», «не позднее ЧЧ:ММ» — срок внутри дня, не время напоминания: не
    разбираются и остаются в названии. Диапазон «с 15:00 до 16:00», «15:00–16:00» — время
    начала, из названия вырезается весь диапазон.
    Точка — только дата: «10.05» — 10 мая, «10:05» — время, «в 12.30» — ни то ни другое.
    Число со словом суммы после него («на 10:30 руб») — не время. Недопустимое время («25:00»,
    «в 13 вечера») остаётся в названии. Время ищется в тексте без найденной даты. Время без даты —
    `NO_DATE`, время в результате сохраняется; одно время без текста — `UNKNOWN`.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from datetime import date, time, timedelta
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
    remind_time: time | None = None  # время из текста (#103); None — времени не было


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

# Время (#103). Число не должно быть частью другого числа: слева не цифра/точка/двоеточие,
# справа — не цифра и не «.5»/«:5»/«,5». Суффикс «ч» не должен быть началом слова («человек»).
_TIME_PATTERN = re.compile(
    r"(?:(?<![\w])(?P<prep>в|во|с|со|к|ко|до)\s+)?"
    r"(?<![\d.:,])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?!\d|[.:,]\d)"
    r"(?:\s*(?P<suf>часов|часа|час|ч\.?|утра|дня|вечера|ночи)(?![а-яёА-ЯЁ]))?",
    re.IGNORECASE,
)
# «до 18:00», «к 9» — срок внутри дня, а не время напоминания: не разбираем (решение по ревью).
_DEADLINE_PREPS = frozenset({"до", "к", "ко", "не позднее"})
_HOUR_SUFFIXES = frozenset({"ч", "час", "часа", "часов"})
# Конец диапазона после начала: «–16:00», «до 16:00».
_RANGE_END = re.compile(r"\s*(?:[-–—]|до\s)\s*\d{1,2}:\d{2}(?![\d.:,])", re.IGNORECASE)
# Голое «в N» — время, если за ним конец текста или знак препинания.
_BARE_END = re.compile(r"\s*(?:$|[,;!?.…)—–-](?!\d))")
# Между голым «в N» и датой — только пробелы и, может быть, предлог даты («в 15 в понедельник»).
_BARE_TO_DATE = re.compile(r"\s*(?:(?:в|во|на|к|ко|до)\s+)?", re.IGNORECASE)


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
        remind_time, spans = _with_time(text, start, end)
        return ParsedTask(
            status=ParseStatus.PARSED,
            title=_build_title(text, spans),
            due_date=due_date,
            explicit_year=explicit_year,
            remind_time=remind_time,
        )

    invalid = [f for f in finds if f is not None and f[0] == "invalid"]
    if invalid:
        _, start, end = min(invalid, key=lambda f: f[1])
        remind_time, spans = _with_time(text, start, end)
        return ParsedTask(
            status=ParseStatus.NO_DATE,
            title=_build_title(text, spans),
            remind_time=remind_time,
        )

    found = _find_time(text)
    if found is not None:
        start, end, remind_time = found
        title = _build_title(text, [(start, end)])
        if not title:
            return ParsedTask(status=ParseStatus.UNKNOWN)  # одно время, без задачи
        # Время рядом с текстом — уже похоже на задачу, даже из одного слова («встреча в 15:00»).
        return ParsedTask(status=ParseStatus.NO_DATE, title=title, remind_time=remind_time)
    return _fallback(text)


def _with_time(text: str, start: int, end: int) -> tuple[time | None, list[tuple[int, int]]]:
    """Время в тексте без даты [start, end) и куски, которые вырезаются из названия."""
    masked = text[:start] + " " * (end - start) + text[end:]
    found = _find_time(masked, date_start=start)
    if found is None:
        return None, [(start, end)]
    t_start, t_end, remind_time = found
    return remind_time, [(start, end), (t_start, t_end)]


def _to_24h(hour: int, minute: int, suffix: str | None, *, leading_zero: bool) -> time | None:
    """Час с суффиксом → время суток; сочетание, которого не бывает, — None.

    Без «утра»/«ночи» часы 1–6 — дневные (решение по ревью #103): «в 3» — 15:00; запись
    с ведущим нулём («03:00») — как написана.
    """
    suffix = (suffix or "").lower().rstrip(".")
    if suffix == "утра":
        ok = 1 <= hour <= 11
    elif suffix == "дня":
        ok = hour == 12 or 1 <= hour <= 5
        if ok and hour != 12:
            hour += 12
    elif suffix == "вечера":
        ok = 4 <= hour <= 11
        hour += 12
    elif suffix == "ночи":
        ok = hour == 12 or 1 <= hour <= 4
        hour %= 12
    else:  # без суффикса, «ч», «час», «часа», «часов»
        ok = 0 <= hour <= 23
        if ok and 1 <= hour <= 6 and not leading_zero:
            hour += 12
    if not ok or not 0 <= minute <= 59:
        return None
    return time(hour, minute)


def _next_word(text: str, pos: int) -> str:
    match = re.match(r"\s*([а-яёА-ЯЁ%]+|\d)", text[pos:])
    return match.group(1).lower() if match else ""


def _deadline_before(text: str, pos: int) -> bool:
    """Перед временем без своего предлога стоит «до / к / не позднее» — это срок, не время."""
    prep = _PREPOSITION_PATTERN.search(text[:pos])
    return prep is not None and prep.group(1).lower() in _DEADLINE_PREPS


def _bare_hour_ok(text: str, end: int, date_start: int | None) -> bool:
    """Голое «в N»: за числом конец текста, знак препинания или сразу найденная дата."""
    if _BARE_END.match(text, end):
        return True
    if date_start is None or date_start < end:
        return False
    return _BARE_TO_DATE.fullmatch(text, end, date_start) is not None


def _find_time(text: str, date_start: int | None = None) -> tuple[int, int, time] | None:
    """Первое время в тексте: (start, end, время). Предлог «в/с» входит в кусок.

    `date_start` — где в тексте начинается найденная дата (её место в `text` замаскировано
    пробелами): голое «в N» прямо перед ней — время («в 15 завтра маме»).
    """
    for m in _TIME_PATTERN.finditer(text):
        raw_hour = m.group("h")
        minutes, suffix = m.group("m"), m.group("suf")
        prep = (m.group("prep") or "").lower()
        norm_suffix = (suffix or "").lower().rstrip(".")
        if prep in _DEADLINE_PREPS or (not prep and _deadline_before(text, m.start())):
            continue  # «до 18:00», «к 9 утра» — срок, остаётся в названии
        if minutes is None:
            if suffix is None:
                if prep not in ("в", "во") or not _bare_hour_ok(text, m.end(), date_start):
                    continue  # «в 2 этапа», «до 15», «купить 3 пачки»
            elif norm_suffix in _HOUR_SUFFIXES and prep not in ("в", "во"):
                continue  # «ждать 3 часа» — длительность, не время
        if _next_word(text, m.end()) in _STOP_WORDS_AFTER:
            continue  # «на 10:30 руб» — сумма
        value = _to_24h(
            int(raw_hour),
            int(minutes or 0),
            suffix,
            leading_zero=len(raw_hour) == 2 and raw_hour.startswith("0"),
        )
        if value is None:
            continue
        end = m.end()
        if minutes is not None:
            span_end = _RANGE_END.match(text, end)
            if span_end is not None:
                end = span_end.end()  # «с 15:00 до 16:00» — время начала, вырезаем весь диапазон
        return m.start(), end, value
    return None


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


def _build_title(text: str, spans: list[tuple[int, int]]) -> str:
    """`{title}`: текст без даты и времени (и предлога перед ними), с заглавной, до 60 символов.

    `spans` — непересекающиеся куски [start, end): дата и, если есть, время (#103).
    """
    remainder = ""
    pos = 0
    for start, end in sorted(spans):
        prep_match = _PREPOSITION_PATTERN.search(text[pos:start])
        strip_start = pos + prep_match.start(1) if prep_match else start
        remainder += text[pos:strip_start] + " "
        pos = end
    remainder += text[pos:]
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
