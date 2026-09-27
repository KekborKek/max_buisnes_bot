"""Напоминания: расписание (T4) и отправка раз в минуту (T6). Логика — docs/spec/reminders.md.

`tick` вызывает фоновый цикл из lifespan (app/main.py) раз в минуту. Отправка устроена
«забрать → отправить → записать»: сеть — только между короткими транзакциями (см. `tick`).
Тексты и кнопки экрана 6 собирает `render_reminder`; им же пользуется кнопка
«Напомнить завтра» (app/bot/handlers/reminders.py), чтобы перерисовать сообщение.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.formatting import (
    count_words,
    date_with_shift,
    format_date,
    join_titles,
    when_words,
)
from app.calendar import loader
from app.calendar.types import ItemType, NotificationKind, Reference
from app.core import events
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import (
    DEFAULT_TIMEZONE,
    Notification,
    Profile,
    Task,
    UserObligation,
    default_reminder_settings,
)
from app.core.texts import t

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


def send_at_utc(day: date, hour: int, tz: str, minute: int = 0) -> datetime:
    """`hour`:`minute` дня `day` по часовому поясу `tz` (IANA) → aware datetime в UTC. (T4)

    `minute` — только у своих задач (#103); обязательства, сводка и snooze — ровно в `hour`:00.
    """
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _enabled_kinds(*, needs_prep: bool, d30: bool, d7: bool) -> frozenset[str]:
    """Виды, включённые для обязательства: d30 — только needs_prep; d1 и overdue — всегда."""
    enabled = {"d1", "overdue"}
    if needs_prep and d30:
        enabled.add("d30")
    if d7:
        enabled.add("d7")
    return frozenset(enabled)


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
    enabled = _enabled_kinds(needs_prep=needs_prep, d30=d30, d7=d7)
    planned = []
    for kind in _OBLIGATION_KINDS:
        if kind not in enabled:
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
    remind_minute: int = 0,
) -> PlannedNotification | None:
    """Ровно одно `task`-уведомление за remind_offset_days до срока (D11); в прошлом — None. (T4)

    Время — `remind_hour`:`remind_minute` по `tz` (#103).
    """
    at = send_at_utc(
        due_date - timedelta(days=remind_offset_days), remind_hour, tz, minute=remind_minute
    )
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
    - pending выключенного вида (d30/d7 выключены в настройках, d30 без needs_prep) → cancelled,
      и с send_at в прошлом, и «в аренде» у планировщика (#97);
    - pending включённого вида, которого нет в плане, потому что новое время уже прошло
      (сменили пояс или час в день d1), — остаётся одна копия со старым send_at: не теряем
      последнее напоминание (решение по #85); если вид уже sent/failed — будущие копии
      → cancelled. snooze не трогаем.
    `settings` — Profile.reminders. Пишет в сессию, не коммитит; у `uo` должен быть id.
    """
    if uo.id is None:
        await session.flush()
    if uo.done_at is not None:
        await cancel_pending(session, "obligation", uo.id)
        return

    opts = reminder_settings(settings)
    enabled = _enabled_kinds(needs_prep=needs_prep, d30=bool(opts["d30"]), d7=bool(opts["d7"]))
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

    for kind, rows in pending.items():
        if kind not in enabled:
            # Вид выключен (d30/d7 в настройках, d30 без needs_prep) — человек отказался от него
            # явно: отменяем всё pending, и с send_at в прошлом (иначе после простоя сервера оно
            # ушло бы), и «аренду» (повтора после ошибки не будет; если сообщение как раз ушло,
            # отправка всё равно запишет `sent` — см. `_record`). #97
            for n in rows:
                n.status = "cancelled"
            continue
        if kind not in finished:
            # Включён, но новое время уже прошло: pending остаётся как было (одна копия).
            # Уже sent/failed — будущие копии отменяются ниже, иначе ушли бы вторым сообщением.
            rows = rows[1:]
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
            remind_minute=task.remind_minute or 0,
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


async def resync_user_notifications(
    session: AsyncSession,
    user_id: int,
    *,
    tz: str,
    settings: Mapping[str, Any] | None,
    reference: Reference,
    now: datetime,
) -> None:
    """Пересчёт pending пользователя после смены пояса или настроек (экран 13, reminders.md).

    - неотмеченные обязательства → `sync_obligation_notifications` с новыми `tz` и `settings`;
      записи, которой нет в справочнике, пропускаем — её pending отменит отправка
      (`_claim` → `load_reminder_item` вернёт None);
    - `task`-уведомления: pending с send_at в будущем и `attempts == 0` получают send_at
      по новому поясу (у задачи свои remind_offset_days, remind_hour и remind_minute). Новое
      время уже прошло — остаётся старое: это единственное напоминание по задаче (D11). В прошлом,
      в отправке или на повторе (`attempts > 0`, см. `_claim`) — не трогаем;
    - задача без `task`-уведомления (по старому поясу время уже прошло) получает его, если по
      новому поясу оно впереди — правилом `sync_task_notification`. Если по задаче уже было
      sent/failed — не создаём: напоминание уже приходило (#97);
    - snooze и digest не трогаем: сводка считается сама каждую неделю.
    Повторный вызов с теми же данными ничего не меняет. Пишет в сессию, не коммитит.
    """
    needs_prep = {ob.id: ob.needs_prep for ob in reference.catalog.obligations}
    uos = await session.scalars(
        select(UserObligation)
        .where(UserObligation.user_id == user_id, UserObligation.done_at.is_(None))
        .order_by(UserObligation.id)
    )
    for uo in list(uos):
        prep = needs_prep.get(uo.obligation_id)
        if prep is None:
            continue
        await sync_obligation_notifications(
            session, uo, needs_prep=prep, tz=tz, settings=settings, now=now
        )

    rows = await session.scalars(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.item_type == "task",
            Notification.kind == "task",
            Notification.status == "pending",
            Notification.attempts == 0,
        )
        .order_by(Notification.id)
    )
    for n in list(rows):
        if as_utc(n.send_at) <= now:
            continue
        task = await session.get(Task, n.item_id)
        if task is None or task.done_at is not None or task.deleted_at is not None:
            continue
        planned = plan_task_notification(
            task.due_date,
            remind_offset_days=task.remind_offset_days,
            remind_hour=task.remind_hour,
            remind_minute=task.remind_minute or 0,
            tz=tz,
            now=now,
        )
        if planned is not None and as_utc(n.send_at) != planned.send_at:
            n.send_at = planned.send_at

    # Недостающее: по старому поясу время уже прошло и строку не создали, а по новому оно
    # впереди. Задачи с pending (любым) и уже напомненные (sent/failed) пропускаем — иначе
    # после смены пояса в тот же день напоминание пришло бы второй раз. Раньше сегодняшнего
    # дня по новому поясу срок — время напоминания точно в прошлом. #97
    today = as_utc(now).astimezone(ZoneInfo(tz)).date()
    covered = set(
        await session.scalars(
            select(Notification.item_id).where(
                Notification.user_id == user_id,
                Notification.item_type == "task",
                Notification.kind == "task",
                Notification.status.in_(("pending", *_FINISHED_STATUSES)),
            )
        )
    )
    tasks = await session.scalars(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.done_at.is_(None),
            Task.deleted_at.is_(None),
            Task.due_date >= today,
        )
        .order_by(Task.id)
    )
    for task in list(tasks):
        if task.id not in covered:
            # То же правило, что при создании задачи: одно `task`, только в будущем.
            await sync_task_notification(session, task, tz=tz, now=now)


# --- Отправка (T6) ------------------------------------------------------------------------------

# Ошибка отправки → одна повторная попытка через час, дальше failed (reminders.md, «Отправка»).
RETRY_DELAY = timedelta(hours=1)
MAX_ATTEMPTS = 2
# Сколько уведомлений забирает один тик: тик не растягивается без предела, остальное — в следующем.
BATCH_LIMIT = 500
# Эти виды не собираются в сводное сообщение: у текста `grouped` нет формулировки
# «срок прошёл» (решение D30) — каждое уходит отдельно.
_NOT_GROUPED = frozenset({"overdue"})
# Сводка в понедельник (экран 12, app/calendar/digest.py): её записи в Notification — отметка
# «за эту неделю отправлялась», их создаёт и закрывает digest.tick. Обычная отправка их
# не забирает, иначе упавшая на полпути сводка ушла бы вторым сообщением.
DIGEST_KIND: NotificationKind = "digest"


@dataclass(frozen=True, slots=True)
class ReminderItem:
    """Одно событие в напоминании — всё, что нужно для текста и кнопок, без ORM."""

    notification_id: int
    item_type: ItemType
    item_id: int
    title: str
    due_date: date
    original_date: date | None  # только у обязательств; у задачи переноса нет
    penalty_text: str = ""


@dataclass(frozen=True, slots=True)
class OutgoingReminder:
    """Готовое к отправке сообщение: одно событие или сводное по одной дате."""

    user_id: int
    kind: NotificationKind
    items: tuple[ReminderItem, ...]
    text: str
    attachments: list[dict] | None

    @property
    def grouped(self) -> bool:
        return len(self.items) > 1

    @property
    def notification_ids(self) -> list[int]:
        return [item.notification_id for item in self.items]


def reminder_payload(action: str, item_type: str, item_id: int) -> str:
    """Payload кнопки экрана 6: `r:<action>:<item_type>:<item_id>` (T7 разбирает done/howto)."""
    return f"r:{action}:{item_type}:{item_id}"


def _open_button(start_param: str | None) -> dict | None:
    """«Открыть календарь» (D27). Без MAX_BOT_USERNAME кнопку не показываем."""
    if not get_settings().max_bot_username:
        return None
    return kb.open_app(t("reminder.btn_open"), start_param)


def reminder_keyboard(
    kind: NotificationKind, item_type: str, item_id: int, *, with_snooze: bool = True
) -> list[dict] | None:
    """Кнопки одиночного напоминания по таблице экрана 6. None — кнопок нет.

    `with_snooze=False` — клавиатура d7 после «Напомнить завтра»: та же, без этой кнопки.
    """

    def cb(action: str) -> dict:
        payload = reminder_payload(action, item_type, item_id)
        return kb.callback(t(f"reminder.btn_{action}"), payload)

    open_btn = _open_button(f"item_{item_type}_{item_id}")
    if kind == "d30":
        rows = [[cb("howto"), open_btn]]
    elif kind == "d7":
        rows = [[cb("done")], [cb("howto"), cb("snooze")] if with_snooze else [cb("howto")]]
    elif kind == "task":
        rows = [[cb("done")], [open_btn]]
    else:  # d1, overdue, snooze
        rows = [[cb("done")], [cb("howto")]]
    rows = [[b for b in row if b is not None] for row in rows]
    rows = [row for row in rows if row]
    return [kb.inline_keyboard(*rows)] if rows else None


def _grouped_date(items: Sequence[ReminderItem], today: date) -> str:
    """Дата сводного: `shift_note` — только если все события перенесены с одной и той же даты."""
    due = items[0].due_date
    originals = {item.original_date for item in items}
    if len(originals) == 1:
        return date_with_shift(due, originals.pop(), today)
    return format_date(due, today)


def render_reminder(
    kind: NotificationKind, items: Sequence[ReminderItem], today: date
) -> tuple[str, list[dict] | None]:
    """Текст и кнопки экрана 6. `today` — сегодня в поясе пользователя (для года и {when}).

    Несколько событий одной даты — вариант `grouped` с одной кнопкой «Открыть календарь»
    без параметра запуска (правило 2 reminders.md).
    """
    if len(items) > 1:
        open_btn = _open_button(None)
        text = t(
            "reminder.grouped",
            when=when_words(items[0].due_date, today),
            date=_grouped_date(items, today),
            count_words=count_words(len(items)),
            titles=join_titles([item.title for item in items]),
        )
        return text, [kb.inline_keyboard([open_btn])] if open_btn else None

    item = items[0]
    fields: dict[str, Any] = {
        "date": date_with_shift(item.due_date, item.original_date, today),
        "title": item.title,
    }
    if kind == "d7":
        fields["penalty"] = item.penalty_text
    if kind == "task":
        fields["when"] = when_words(item.due_date, today)
    text = t(f"reminder.{kind}", **fields)
    return text, reminder_keyboard(kind, item.item_type, item.item_id)


def user_today(profile: Profile | None, now: datetime) -> tuple[date, str]:
    """Сегодня в поясе пользователя и сам пояс. Пояс не выбран — московский (экран 6)."""
    tz = (profile.timezone if profile is not None else None) or DEFAULT_TIMEZONE
    return as_utc(now).astimezone(ZoneInfo(tz)).date(), tz


async def load_reminder_item(
    session: AsyncSession,
    reference: Reference,
    *,
    notification_id: int,
    user_id: int,
    item_type: str,
    item_id: int,
) -> ReminderItem | None:
    """Событие для напоминания, если по нему ещё надо напоминать; иначе None.

    None — события нет (удалено при пересборке, задача удалена), оно чужое, уже отмечено
    или записи нет в справочнике. Только чтение.
    """
    if item_type == "obligation":
        uo = await session.get(UserObligation, item_id)
        if uo is None or uo.user_id != user_id or uo.done_at is not None:
            return None
        ob = next((o for o in reference.catalog.obligations if o.id == uo.obligation_id), None)
        if ob is None:
            log.warning("напоминание %s: %s нет в справочнике", notification_id, uo.obligation_id)
            return None
        return ReminderItem(
            notification_id=notification_id,
            item_type="obligation",
            item_id=uo.id,
            title=ob.title,
            due_date=uo.due_date,
            original_date=uo.original_date,
            penalty_text=ob.penalty_text,
        )
    if item_type == "task":
        task = await session.get(Task, item_id)
        if (
            task is None
            or task.user_id != user_id
            or task.done_at is not None
            or task.deleted_at is not None
        ):
            return None
        return ReminderItem(
            notification_id=notification_id,
            item_type="task",
            item_id=task.id,
            title=task.title,
            due_date=task.due_date,
            original_date=None,
        )
    log.warning("напоминание %s: неизвестный item_type %r", notification_id, item_type)
    return None


def _is_stale(kind: str, due_date: date, today: date) -> bool:
    """Текст уже неверен: «Через месяц» / «Через 7 дней» / «Завтра» — только в свой день.

    Бывает, если планировщик стоял (деплой, сервер лежал): не шлём «через 30 дней», когда
    осталось 12 (reminders.md, тот же принцип, что при создании). Такое → cancelled.
    overdue и snooze от дня не зависят; задача — пока срок не прошёл.
    """
    days_left = (due_date - today).days
    if kind in ("d30", "d7", "d1"):
        return days_left != -_KIND_OFFSET_DAYS[kind]
    if kind == "task":
        return days_left < 0
    return False


async def _claim(now: datetime, reference: Reference) -> list[OutgoingReminder]:
    """Короткая транзакция «забрать»: pending с send_at ≤ now → готовые сообщения.

    Первый оператор транзакции — UPDATE … RETURNING: транзакция сразу пишущая, и SQLite
    выдаёт строки ровно одному тику. Забранное получает `attempts + 1` и `send_at = now + 1 ч`
    («аренда»): параллельный или следующий тик его уже не выберет, а если процесс упадёт
    до записи результата, уведомление само вернётся через час как повторная попытка.
    Отмеченное и удалённое — сразу `cancelled`. Сетевых вызовов внутри нет.
    """
    lease_until = now + RETRY_DELAY
    due_ids = (
        select(Notification.id)
        .where(
            Notification.status == "pending",
            Notification.send_at <= now,
            Notification.kind != DIGEST_KIND,
        )
        .order_by(Notification.send_at, Notification.id)
        .limit(BATCH_LIMIT)
        .scalar_subquery()
    )
    claim = (
        update(Notification)
        .where(
            Notification.id.in_(due_ids),
            Notification.status == "pending",
            Notification.send_at <= now,
            Notification.kind != DIGEST_KIND,
        )
        .values(attempts=Notification.attempts + 1, send_at=lease_until)
        .returning(
            Notification.id,
            Notification.user_id,
            Notification.item_type,
            Notification.item_id,
            Notification.kind,
        )
        .execution_options(synchronize_session=False)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(claim)).all()
        if not rows:
            await session.commit()
            return []

        groups: dict[tuple, list[ReminderItem]] = {}
        todays: dict[int, date] = {}
        cancelled: list[int] = []
        for row in sorted(rows, key=lambda r: r.id):
            item = await load_reminder_item(
                session,
                reference,
                notification_id=row.id,
                user_id=row.user_id,
                item_type=row.item_type,
                item_id=row.item_id,
            )
            if item is None:
                cancelled.append(row.id)
                continue
            if row.user_id not in todays:
                todays[row.user_id], _ = user_today(await session.get(Profile, row.user_id), now)
            if _is_stale(row.kind, item.due_date, todays[row.user_id]):
                log.info("напоминание %s (%s) устарело — не отправляем", row.id, row.kind)
                cancelled.append(row.id)
                continue
            # Правило 2: одно сообщение на (user_id, kind, due_date); overdue — поштучно (D30).
            tail = row.id if row.kind in _NOT_GROUPED else None
            groups.setdefault((row.user_id, row.kind, item.due_date, tail), []).append(item)

        if cancelled:
            await session.execute(
                update(Notification)
                .where(Notification.id.in_(cancelled))
                .values(status="cancelled")
                .execution_options(synchronize_session=False)
            )
        await session.commit()

    outgoing = []
    for (user_id, kind, _due, _tail), items in groups.items():
        text, attachments = render_reminder(kind, items, todays[user_id])
        outgoing.append(
            OutgoingReminder(
                user_id=user_id, kind=kind, items=tuple(items), text=text, attachments=attachments
            )
        )
    return outgoing


async def _record(reminder: OutgoingReminder, *, ok: bool, now: datetime) -> None:
    """Короткая транзакция «записать» после отправки одного сообщения.

    Успех → `sent`, `send_at` = момент тика (аренду убираем, история честная), `reminder_sent`
    на каждое событие. `cancelled` тоже становится `sent`: `_claim` забирает только pending,
    значит строку отменили, пока сообщение летело (выключили вид, отметили, удалили), а оно
    всё равно ушло — история честная, и пересчёт не пришлёт этот вид второй раз (#97).
    Ошибка → при `attempts < MAX_ATTEMPTS` строка остаётся pending с арендой = повтор через
    час, иначе `failed`. `status == 'pending'` в условии: отменённое за время полёта
    остаётся `cancelled` и повторно не уходит.
    """
    ids = reminder.notification_ids
    async with SessionLocal() as session:
        if ok:
            await session.execute(
                update(Notification)
                .where(
                    Notification.id.in_(ids),
                    Notification.status.in_(("pending", "cancelled")),
                )
                .values(status="sent", send_at=now)
                .execution_options(synchronize_session=False)
            )
            for item in reminder.items:
                await events.track(
                    session,
                    reminder.user_id,
                    "reminder_sent",
                    {
                        "kind": reminder.kind,
                        "item_id": item.item_id,
                        "item_type": item.item_type,
                        "grouped": reminder.grouped,
                    },
                )
        else:
            await session.execute(
                update(Notification)
                .where(
                    Notification.id.in_(ids),
                    Notification.status == "pending",
                    Notification.attempts >= MAX_ATTEMPTS,
                )
                .values(status="failed")
                .execution_options(synchronize_session=False)
            )
            props = {"where": "reminder_send", "kind": "send_failed"}
            await events.track(session, reminder.user_id, "error", props)
        await session.commit()


async def deliver(reminder: OutgoingReminder, max_client: MaxClient, now: datetime) -> bool:
    """Отправить одно готовое напоминание и записать результат. True — ушло.

    Вызывать без открытой транзакции: здесь запрос к MAX, потом своя короткая транзакция.
    Этим же пользуется демо-команда `/demo_remind` (T13).
    """
    try:
        await max_client.send_message(
            reminder.text, user_id=reminder.user_id, attachments=reminder.attachments, fmt=None
        )
        ok = True
    except Exception:
        log.exception(
            "напоминание %s пользователю %s не отправлено",
            reminder.notification_ids,
            reminder.user_id,
        )
        ok = False
    try:
        await _record(reminder, ok=ok, now=now)
    except Exception:
        # Не записали — строка осталась pending с арендой: через час будет повтор.
        log.exception("не записан результат отправки %s", reminder.notification_ids)
    return ok


async def _has_due(now: datetime) -> bool:
    """Есть ли что отправлять. Только чтение, без транзакции записи."""
    async with SessionLocal() as session:
        found = await session.scalar(
            select(Notification.id)
            .where(
                Notification.status == "pending",
                Notification.send_at <= now,
                Notification.kind != DIGEST_KIND,
            )
            .limit(1)
        )
    return found is not None


async def tick(now: datetime, max_client: MaxClient, *, reference: Reference | None = None) -> None:
    """Один проход планировщика (T6): pending с send_at ≤ now → отправка.

    «Забрать» (`_claim`, короткая транзакция) → отправка без транзакции → «записать»
    (`_record`, короткая транзакция на каждое сообщение). Запрос к MAX (до 35 с) не держит
    блокировку записи SQLite; два тика одно уведомление не заберут — см. `_claim`.

    Справочник читается, только когда есть что отправлять: пустой тик не зависит от файлов
    аналитика. `reference` — для тестов. Исключение отсюда цикл логирует и продолжает работу.

    После обычных напоминаний — сводка в понедельник (экран 12, `digest.tick`).
    """
    # Импорт здесь: digest.py импортирует этот модуль на уровне модуля — иначе цикл.
    from app.calendar import digest

    now = as_utc(now)
    if await _has_due(now):
        reference = reference if reference is not None else loader.get_reference()
        for reminder in await _claim(now, reference):
            await deliver(reminder, max_client, now)
    await digest.tick(now, max_client, reference=reference)
