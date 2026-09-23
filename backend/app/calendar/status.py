"""Статус события календаря (product.md, «Статусы и категории»).

Статус вычисляется, а не хранится: из срока, отметки и «сегодня» в часовом поясе
пользователя. Считает только бэкенд — мини-апп его показывает и не пересчитывает.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.calendar.types import ItemStatus
from app.core.models import DEFAULT_TIMEZONE

log = logging.getLogger(__name__)


def today_in(tz: str | None, now: datetime) -> date:
    """Календарная дата момента `now` (aware) в поясе `tz` (IANA).

    Пустой или неизвестный пояс — Europe/Moscow: профиль по умолчанию (product.md).
    """
    try:
        zone = ZoneInfo(tz or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Неизвестный часовой пояс %r, считаю по %s", tz, DEFAULT_TIMEZONE)
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return now.astimezone(zone).date()


def item_status(due_date: date, done_at: datetime | None, today: date) -> ItemStatus:
    """done — есть отметка; иначе overdue / today / upcoming по сравнению срока с `today`."""
    if done_at is not None:
        return "done"
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    return "upcoming"
