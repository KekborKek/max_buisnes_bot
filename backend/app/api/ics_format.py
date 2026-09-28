"""Текст iCalendar (RFC 5545) для ленты «Добавить в календарь телефона» — без зависимостей.

Только то, что нужно ленте: целодневные VEVENT и одно напоминание VALARM на событие.
- строки разделяются CRLF (§3.1), длинные складываются по 75 октетам UTF-8, продолжение
  начинается с пробела, многобайтовый символ не разрезается;
- в значениях TEXT экранируются `\\ ; ,` и переводы строк (§3.3.11);
- целодневное событие — `DTSTART;VALUE=DATE` и `DTEND` следующим днём (§3.6.1: DTEND
  не включается), без пояса: день один и тот же в любом часовом поясе телефона.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

CRLF = "\r\n"
LINE_OCTETS = 75  # §3.1: строка не длиннее 75 октетов без CRLF

PRODID = "-//Maksimy na parkovke//MAX Calendar IP//RU"

# Управляющие символы, запрещённые в TEXT (§3.3.11); TAB и переводы строк обрабатываются отдельно
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NEWLINES = re.compile(r"[\r\n]")


@dataclass(frozen=True)
class FeedEvent:
    """Одно событие ленты. `alarm_before_min` — за сколько минут до начала дня (00:00)
    напомнить; отрицательное — после начала (задача «в тот же день, 10:00» → -600).
    None — без напоминания."""

    uid: str
    day: date
    summary: str
    description: str | None = None
    url: str | None = None
    alarm_before_min: int | None = None


def escape_text(value: str) -> str:
    """Значение TEXT: `\\` → `\\\\`, `;` → `\\;`, `,` → `\\,`, перевод строки → `\\n`.

    Прочие управляющие символы (кроме TAB) вырезаются: в TEXT они запрещены.
    """
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _CONTROL.sub("", value)
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\n", "\\n")


def fold(line: str) -> str:
    """Складывает строку по 75 октетам UTF-8 (§3.1); символ целиком остаётся в одной части.

    Продолжение начинается с пробела, и пробел входит в 75 октетов его строки.
    """
    parts: list[str] = []
    current: list[str] = []
    size = 0
    limit = LINE_OCTETS
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > limit:
            parts.append("".join(current))
            current, size, limit = [], 0, LINE_OCTETS - 1
        current.append(ch)
        size += width
    parts.append("".join(current))
    return (CRLF + " ").join(parts)


def format_date(day: date) -> str:
    return day.strftime("%Y%m%d")


def format_utc(moment: datetime) -> str:
    """DATE-TIME в UTC с суффиксом Z (форма 2, §3.3.5)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def format_trigger(before_min: int) -> str:
    """Смещение VALARM от начала события (§3.3.6): 900 → `-PT15H`, 7×1440+… → `-P6DT14H`."""
    sign = "-" if before_min > 0 else ""
    total = abs(before_min)
    if total == 0:
        return "PT0S"
    days, rest = divmod(total, 1440)
    hours, minutes = divmod(rest, 60)
    out = f"{sign}P"
    if days:
        out += f"{days}D"
    if hours or minutes:
        out += "T"
        if hours:
            out += f"{hours}H"
        if minutes:
            out += f"{minutes}M"
    return out


def _event_lines(event: FeedEvent, stamp: str) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{format_date(event.day)}",
        f"DTEND;VALUE=DATE:{format_date(event.day + timedelta(days=1))}",
        f"SUMMARY:{escape_text(event.summary)}",
    ]
    if event.description:
        lines.append(f"DESCRIPTION:{escape_text(event.description)}")
    if event.url:
        lines.append(f"URL:{_NEWLINES.sub('', event.url)}")
    # Целодневный срок не занимает время в расписании
    lines.append("TRANSP:TRANSPARENT")
    if event.alarm_before_min is not None:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{escape_text(event.summary)}",  # обязателен для DISPLAY (§3.6.6)
            f"TRIGGER:{format_trigger(event.alarm_before_min)}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def render_calendar(events: Iterable[FeedEvent], *, name: str, now: datetime) -> str:
    """VCALENDAR целиком: каждая строка сложена по 75 октетам и заканчивается CRLF."""
    stamp = format_utc(now)
    lines = [
        "BEGIN:VCALENDAR",
        f"PRODID:{PRODID}",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(name)}",
        # Подсказка подписчикам, как часто обновлять (RFC 7986 и её предшественник у Apple)
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for event in events:
        lines += _event_lines(event, stamp)
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + CRLF for line in lines)
