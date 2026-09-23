"""Напоминания: расписание (T4) и отправка раз в минуту (T6). Логика — docs/spec/reminders.md.

`tick` вызывает фоновый цикл из lifespan (app/main.py) раз в минуту. Пока T6 не сделана,
`tick` ничего не делает — это заглушка, а не NotImplementedError, чтобы не сыпать ошибками
в лог каждую минуту.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.types import ItemType, NotificationKind
from app.core.models import Notification, Task, UserObligation, default_reminder_settings

if TYPE_CHECKING:
    from app.core.max_client import MaxClient

log = logging.getLogger(__name__)

# Плановые виды по обязательству (правило 4). snooze сюда не входит: его создаёт кнопка
# «Напомнить завтра» (T6/T7), и синхронизация его не трогает.
_OBLIGATION_KINDS: tuple[NotificationKind, ...] = ("d30", "d7", "d1", "overdue")
# Смещение дня отправки от due_date.
_KIND_OFFSET_DAYS: dict[str, int] = {"d30": -30, "d7": -7, "d1": -1, "overdue": 1}
# Уже ушедшее (или окончательно не ушедшее) повторно не создаём: иначе смена пояса или
# пересборка могла бы прислать то же напоминание второй раз.
_FINISHED_STATUSES = frozenset({"sent", "failed"})


@dataclass(frozen=True, slots=True)
class PlannedNotification:
    kind: NotificationKind
    send_at: datetime  # aware UTC


def as_utc(value: datetime) -> datetime:
    """SQLite отдаёт DateTime(timezone=True) без пояса; в базе всё в UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def reminder_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Profile.reminders поверх значений по умолчанию (отсутствующий ключ — по умолчанию)."""
    merged = default_reminder_settings()
    if raw:
        merged.update({k: v for k, v in raw.items() if k in merged and v is not None})
    return merged


def send_at_utc(day: date, hour: int, tz: str) -> datetime:
    """`hour`:00 дня `day` по часовому поясу `tz` (IANA) → aware datetime в UTC. (T4)"""
    return datetime.combine(day, time(hour), tzinfo=ZoneInfo(tz)).astimezone(UTC)


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
    enabled = {"d30": needs_prep and d30, "d7": d7, "d1": True, "overdue": True}
    planned = []
    for kind in _OBLIGATION_KINDS:
        if not enabled[kind]:
            continue
        at = send_at_utc(due_date + timedelta(days=_KIND_OFFSET_DAYS[kind]), hour, tz)
        if at > now:
            planned.append(PlannedNotification(kind=kind, send_at=at))
    return planned


def plan_task_notification(
    due_date: date,
    *,
    remind_offset_days: int,
    remind_hour: int,
    tz: str,
    now: datetime,
) -> PlannedNotification | None:
    """Ровно одно `task`-уведомление за remind_offset_days до срока (D11); в прошлом — None. (T4)"""
    at = send_at_utc(due_date - timedelta(days=remind_offset_days), remind_hour, tz)
    if at <= now:
        return None
    return PlannedNotification(kind="task", send_at=at)


async def cancel_pending(session: AsyncSession, item_type: ItemType, item_id: int) -> int:
    """Все pending-уведомления события → cancelled (правило 1). Возвращает число. Не коммитит."""
    result = await session.execute(
        update(Notification)
        .where(
            Notification.item_type == item_type,
            Notification.item_id == item_id,
            Notification.status == "pending",
        )
        .values(status="cancelled")
        .execution_options(synchronize_session="fetch")
    )
    return result.rowcount or 0


async def _item_notifications(
    session: AsyncSession, item_type: ItemType, item_id: int
) -> list[Notification]:
    rows = await session.scalars(
        select(Notification)
        .where(Notification.item_type == item_type, Notification.item_id == item_id)
        .order_by(Notification.id)
    )
    return list(rows)


async def sync_obligation_notifications(
    session: AsyncSession,
    uo: UserObligation,
    *,
    needs_prep: bool,
    tz: str,
    settings: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    """Приводит pending-уведомления обязательства к плану; повторный вызов ничего не меняет.

    Кто вызывает: сборка календаря (build_calendar, экран 5), снятие отметки (экран 8, 16 —
    T7, T10), смена пояса или настроек (экраны 13, 19). Отметку ставят через cancel_pending.

    - отмечено (`done_at`) → все pending отменяются, новых нет;
    - вид из плана уже pending → остаётся одно, `send_at` поправлен (смена пояса), лишние
      копии → cancelled; уже sent/failed → заново не создаётся;
    - pending вида, выпавшего из плана, с send_at в будущем → cancelled (выключили d30);
      с send_at в прошлом — не трогаем, его отправит планировщик; snooze не трогаем.
    `settings` — Profile.reminders. Пишет в сессию, не коммитит; у `uo` должен быть id.
    """
    if uo.id is None:
        await session.flush()
    if uo.done_at is not None:
        await cancel_pending(session, "obligation", uo.id)
        return

    opts = reminder_settings(settings)
    planned = plan_obligation_notifications(
        uo.due_date,
        needs_prep=needs_prep,
        tz=tz,
        now=now,
        hour=int(opts["hour"]),
        d30=bool(opts["d30"]),
        d7=bool(opts["d7"]),
    )
    existing = await _item_notifications(session, "obligation", uo.id)
    pending: dict[str, list[Notification]] = {}
    finished: set[str] = set()
    for n in existing:
        if n.kind not in _OBLIGATION_KINDS:
            continue
        if n.status == "pending":
            pending.setdefault(n.kind, []).append(n)
        elif n.status in _FINISHED_STATUSES:
            finished.add(n.kind)

    for p in planned:
        rows = pending.pop(p.kind, [])
        if p.kind in finished:
            for extra in rows:
                extra.status = "cancelled"
            continue
        if rows:
            keep, *extras = rows
            if as_utc(keep.send_at) != p.send_at:
                keep.send_at = p.send_at
            for extra in extras:
                extra.status = "cancelled"
        else:
            session.add(
                Notification(
                    user_id=uo.user_id,
                    item_type="obligation",
                    item_id=uo.id,
                    kind=p.kind,
                    send_at=p.send_at,
                    status="pending",
                    attempts=0,
                )
            )

    for rows in pending.values():
        for n in rows:
            if as_utc(n.send_at) > now:
                n.status = "cancelled"


async def sync_task_notification(
    session: AsyncSession, task: Task, *, tz: str, now: datetime
) -> None:
    """Одно pending `task`-уведомление по задаче; старое при изменении → cancelled (D11).

    Кто вызывает: создание и изменение задачи (экраны 9, 17 — T8b, T10), снятие отметки,
    смена пояса. Задача отмечена или удалена → все pending отменяются. Повторный вызов с теми
    же данными ничего не меняет. Пишет в сессию, не коммитит; у `task` должен быть id.
    """
    if task.id is None:
        await session.flush()
    planned = None
    if task.done_at is None and task.deleted_at is None:
        planned = plan_task_notification(
            task.due_date,
            remind_offset_days=task.remind_offset_days,
            remind_hour=task.remind_hour,
            tz=tz,
            now=now,
        )
    existing = [
        n for n in await _item_notifications(session, "task", task.id) if n.status == "pending"
    ]
    keep = None
    if planned is not None:
        keep = next((n for n in existing if as_utc(n.send_at) == planned.send_at), None)
    for n in existing:
        if n is not keep:
            n.status = "cancelled"
    if planned is not None and keep is None:
        session.add(
            Notification(
                user_id=task.user_id,
                item_type="task",
                item_id=task.id,
                kind="task",
                send_at=planned.send_at,
                status="pending",
                attempts=0,
            )
        )


async def tick(now: datetime, max_client: MaxClient) -> None:
    """Один проход планировщика (T6): pending с send_at ≤ now → отправка.

    Сессии открывает сам и коротко (app.core.db.SessionLocal): запрос к MAX — вне транзакции.
    Исключение отсюда цикл логирует и продолжает работу.
    """
    return None
