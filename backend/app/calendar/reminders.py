"""Напоминания: расписание (T4) и отправка раз в минуту (T6). Логика — docs/spec/reminders.md.

`tick` вызывает фоновый цикл из lifespan (app/main.py) раз в минуту. Пока T6 не сделана,
`tick` ничего не делает — это заглушка, а не NotImplementedError, чтобы не сыпать ошибками
в лог каждую минуту.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.types import ItemType, NotificationKind

if TYPE_CHECKING:
    from app.core.max_client import MaxClient

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlannedNotification:
    kind: NotificationKind
    send_at: datetime  # aware UTC


def send_at_utc(day: date, hour: int, tz: str) -> datetime:
    """`hour`:00 дня `day` по часовому поясу `tz` (IANA) → aware datetime в UTC. (T4)"""
    raise NotImplementedError("T4")


def plan_obligation_notifications(
    due_date: date,
    *,
    needs_prep: bool,
    tz: str,
    now: datetime,
    hour: int = 10,
    d30: bool = True,
    d7: bool = True,
) -> list[PlannedNotification]:
    """Уведомления по обязательству: d30 (только needs_prep и d30), d7 (если d7), d1, overdue.

    overdue — на следующий день после срока (D10). Всё в `hour` по `tz`; с send_at ≤ now —
    не включаются. `hour`, `d30`, `d7` — из Profile.reminders. (T4)
    """
    raise NotImplementedError("T4")


def plan_task_notification(
    due_date: date,
    *,
    remind_offset_days: int,
    remind_hour: int,
    tz: str,
    now: datetime,
) -> PlannedNotification | None:
    """Ровно одно `task`-уведомление за remind_offset_days до срока (D11); в прошлом — None. (T4)"""
    raise NotImplementedError("T4")


async def cancel_pending(session: AsyncSession, item_type: ItemType, item_id: int) -> int:
    """Все pending-уведомления события → cancelled (правило 1). Возвращает число. Не коммитит."""
    raise NotImplementedError("T4")


async def tick(now: datetime, max_client: MaxClient) -> None:
    """Один проход планировщика (T6): pending с send_at ≤ now → отправка.

    Сессии открывает сам и коротко (app.core.db.SessionLocal): запрос к MAX — вне транзакции.
    Исключение отсюда цикл логирует и продолжает работу.
    """
    return None
