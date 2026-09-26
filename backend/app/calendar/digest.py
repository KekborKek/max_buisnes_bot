"""Экран 12: сводка на неделю в понедельник (docs/screens/should-12-13-19.md; T14, #77).

`tick` зовёт `app.calendar.reminders.tick` раз в минуту. Сводка уходит:
- только тем, у кого собран календарь (`Profile.calendar_built_at`) — онбординг пройден;
- если её не выключили кнопкой «Без сводки» (`Profile.reminders["digest"] is False`;
  нет ключа — включена);
- в понедельник, начиная с часа `reminder_settings(...)["hour"]` по `Profile.timezone`.
  Если планировщик стоял, сводка догоняет в тот же понедельник; во вторник — уже нет;
- если календарь собран не позже планового момента этой недели (онбординг в понедельник
  днём — сводки в этот понедельник нет, человек только что видел экран 5);
- если на неделе (пн–вс) есть хотя бы одно событие без отметки. Пустая неделя — молчим,
  и решение по неделе принимается один раз: строка `cancelled` (см. `_claim`).

Не больше одной сводки на пользователя в неделю. Перед отправкой одной операцией
`INSERT … SELECT … WHERE NOT EXISTS` создаётся `Notification(kind="digest", item_type="week",
item_id=<ГГГГММДД понедельника>)`; есть запись за эту неделю в любом статусе — сводки нет.
Повтора после ошибки нет (решение планировщика по #77): лучше потерять сводку, чем прислать
дубль. Упали между «забрать» и «записать» — строка остаётся `pending`, обычная отправка
напоминаний её не трогает (`reminders._claim` пропускает `kind="digest"`).

Сеть — только между короткими транзакциями, как в reminders.py.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot.formatting import count_words, format_date
from app.calendar import loader
from app.calendar.reminders import DIGEST_KIND, as_utc, reminder_settings, send_at_utc
from app.calendar.types import ItemType, NotificationKind, Reference
from app.core import events
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import DEFAULT_TIMEZONE, Notification, Profile, Task, UserObligation
from app.core.texts import t

if TYPE_CHECKING:
    from app.core.max_client import MaxClient

log = logging.getLogger(__name__)

KIND: NotificationKind = DIGEST_KIND
# item_type записи-отметки «сводка за неделю отправлялась»; item_id — ГГГГММДД понедельника.
ITEM_TYPE = "week"
# Кнопка «Без сводки» (app/bot/handlers/digest.py).
OFF_PAYLOAD = "digest:off"
_WEEK_DAYS = 7


@dataclass(frozen=True, slots=True)
class DigestItem:
    """Одно событие недели в сводке — без ORM."""

    item_type: ItemType
    item_id: int
    title: str
    due_date: date


@dataclass(frozen=True, slots=True)
class OutgoingDigest:
    user_id: int
    monday: date
    send_at: datetime  # плановый момент сводки, aware UTC
    items: tuple[DigestItem, ...]
    text: str
    attachments: list[dict] | None


def digest_enabled(raw: Mapping[str, Any] | None) -> bool:
    """Сводка включена, пока в Profile.reminders нет `"digest": false`.

    Читаем сырое значение: `reminder_settings` отбрасывает ключи, которых нет в значениях
    по умолчанию, и `digest` через неё не виден.
    """
    return (raw or {}).get("digest") is not False


def week_monday(day: date) -> date:
    """Понедельник недели, в которую входит `day`."""
    return day - timedelta(days=day.weekday())


def week_key(monday: date) -> int:
    """item_id записи сводки: понедельник как число ГГГГММДД (20261026)."""
    return monday.year * 10000 + monday.month * 100 + monday.day


def format_range(monday: date, today: date) -> str:
    """`{range}`: «26–31 октября», «26 октября – 1 ноября»; год — только если он не текущий."""
    sunday = monday + timedelta(days=_WEEK_DAYS - 1)
    if (monday.year, monday.month) == (sunday.year, sunday.month):
        return f"{monday.day}–{format_date(sunday, today)}"
    return f"{format_date(monday, today)} – {format_date(sunday, today)}"


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def digest_keyboard() -> list[dict]:
    """«Открыть календарь» (без MAX_BOT_USERNAME не показываем, D27) и «Без сводки» в одном ряду."""
    row = []
    if get_settings().max_bot_username:
        row.append(kb.open_app(t("digest.btn_open")))
    row.append(kb.callback(t("digest.btn_off"), OFF_PAYLOAD))
    return [kb.inline_keyboard(row)]


def render_digest(items: Sequence[DigestItem], monday: date, today: date) -> tuple[str, list[dict]]:
    """Текст и кнопки экрана 12. `items` не пустой и уже отсортирован по дате."""
    text = t(
        "digest.body",
        range=format_range(monday, today),
        count_words=_capitalize(count_words(len(items))),
        items=t("digest.items_sep").join(
            t("digest.item", date=format_date(item.due_date, today), title=item.title)
            for item in items
        ),
    )
    return text, digest_keyboard()


async def load_week_items(
    session: AsyncSession, reference: Reference, user_id: int, monday: date
) -> list[DigestItem]:
    """События недели пн–вс без отметки: обязательства и свои задачи (не удалённые), по дате.

    Обязательство, которого уже нет в справочнике, пропускаем, как напоминания. Только чтение.
    """
    sunday = monday + timedelta(days=_WEEK_DAYS - 1)
    titles = {ob.id: ob.title for ob in reference.catalog.obligations}
    items: list[DigestItem] = []
    obligations = await session.scalars(
        select(UserObligation).where(
            UserObligation.user_id == user_id,
            UserObligation.done_at.is_(None),
            UserObligation.due_date >= monday,
            UserObligation.due_date <= sunday,
        )
    )
    for uo in obligations:
        title = titles.get(uo.obligation_id)
        if title is None:
            log.warning("сводка %s: %s нет в справочнике", user_id, uo.obligation_id)
            continue
        items.append(DigestItem("obligation", uo.id, title, uo.due_date))
    tasks = await session.scalars(
        select(Task).where(
            Task.user_id == user_id,
            Task.done_at.is_(None),
            Task.deleted_at.is_(None),
            Task.due_date >= monday,
            Task.due_date <= sunday,
        )
    )
    items.extend(DigestItem("task", task.id, task.title, task.due_date) for task in tasks)
    items.sort(key=lambda item: (item.due_date, item.item_type == "task", item.item_id))
    return items


@dataclass(frozen=True, slots=True)
class _Candidate:
    user_id: int
    monday: date  # сегодня в поясе пользователя
    send_at: datetime


def _due_candidate(
    user_id: int,
    tz: str | None,
    raw: Mapping[str, Any] | None,
    built_at: datetime | None,
    now: datetime,
) -> _Candidate | None:
    """Пользователю пора получить сводку: у него понедельник и час сводки наступил.

    Календарь собран позже планового момента этой недели (онбординг в понедельник днём) —
    сводки за эту неделю нет: только что человек видел экран 5. Битые пояс или час у одного
    пользователя — предупреждение в лог и пропуск только его.
    """
    if built_at is None or not digest_enabled(raw):
        return None
    tz = tz or DEFAULT_TIMEZONE
    try:
        local = now.astimezone(ZoneInfo(tz))
        if local.weekday() != 0:
            return None
        hour = int(reminder_settings(raw)["hour"])
        monday = local.date()
        send_at = send_at_utc(monday, hour, tz)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        log.warning("сводка %s: неверный пояс %r или час в настройках %r", user_id, tz, raw)
        return None
    if now < send_at or as_utc(built_at) > send_at:
        return None
    return _Candidate(user_id, monday, send_at)


async def _candidates(now: datetime) -> list[_Candidate]:
    """Кому пора и по чьей неделе решения ещё не было (нет строки digest). Только чтение."""
    async with SessionLocal() as session:
        rows = await session.execute(
            select(
                Profile.user_id, Profile.timezone, Profile.reminders, Profile.calendar_built_at
            ).where(Profile.calendar_built_at.is_not(None))
        )
        due = [
            c
            for c in (
                _due_candidate(r.user_id, r.timezone, r.reminders, r.calendar_built_at, now)
                for r in rows
            )
            if c is not None
        ]
        if not due:
            return []
        done = set(
            (
                await session.execute(
                    select(Notification.user_id, Notification.item_id).where(
                        Notification.kind == KIND,
                        Notification.user_id.in_([c.user_id for c in due]),
                        Notification.item_id.in_({week_key(c.monday) for c in due}),
                    )
                )
            ).tuples()
        )
    return [c for c in due if (c.user_id, week_key(c.monday)) not in done]


async def _prepare(candidate: _Candidate, reference: Reference) -> OutgoingDigest | None:
    """Сообщение для пользователя или None — на неделе нет событий. Только чтение."""
    async with SessionLocal() as session:
        items = await load_week_items(session, reference, candidate.user_id, candidate.monday)
    if not items:
        return None
    text, attachments = render_digest(items, candidate.monday, candidate.monday)
    return OutgoingDigest(
        user_id=candidate.user_id,
        monday=candidate.monday,
        send_at=candidate.send_at,
        items=tuple(items),
        text=text,
        attachments=attachments,
    )


async def _claim(candidate: _Candidate, now: datetime, *, status: str) -> int | None:
    """Короткая транзакция «забрать»: строка сводки за неделю, если её ещё нет. → id или None.

    Одна операция INSERT … SELECT … WHERE NOT EXISTS: проверка и вставка атомарны в SQLite,
    два тика одну неделю не заберут. `status="pending"` — сводку сейчас отправим;
    `"cancelled"` — неделя пустая, решение принято один раз: позже появившаяся задача
    сводку в этот понедельник не вызовет, и события не перечитываются каждую минуту.
    """
    key = week_key(candidate.monday)
    already = exists().where(
        Notification.user_id == candidate.user_id,
        Notification.kind == KIND,
        Notification.item_id == key,
    )
    columns = (
        Notification.user_id,
        Notification.item_type,
        Notification.item_id,
        Notification.kind,
        Notification.send_at,
        Notification.status,
        Notification.attempts,
        Notification.created_at,
    )
    values = select(
        literal(candidate.user_id, Notification.user_id.type),
        literal(ITEM_TYPE, Notification.item_type.type),
        literal(key, Notification.item_id.type),
        literal(KIND, Notification.kind.type),
        literal(candidate.send_at, Notification.send_at.type),
        literal(status, Notification.status.type),
        literal(1 if status == "pending" else 0, Notification.attempts.type),
        literal(now, Notification.created_at.type),
    ).where(~already)
    stmt = (
        insert(Notification)
        .from_select([c.key for c in columns], values)
        .returning(Notification.id)
    )
    async with SessionLocal() as session:
        claimed = (await session.execute(stmt)).scalar()
        await session.commit()
    return claimed


async def _record(digest: OutgoingDigest, notification_id: int, *, ok: bool, now: datetime) -> None:
    """Короткая транзакция «записать»: `sent` + `reminder_sent` на каждый пункт, иначе `failed`."""
    values: dict[str, Any] = {"status": "sent", "send_at": now} if ok else {"status": "failed"}
    async with SessionLocal() as session:
        await session.execute(
            update(Notification)
            .where(Notification.id == notification_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if ok:
            grouped = len(digest.items) > 1
            for item in digest.items:
                await events.track(
                    session,
                    digest.user_id,
                    "reminder_sent",
                    {
                        "kind": KIND,
                        "item_id": item.item_id,
                        "item_type": item.item_type,
                        "grouped": grouped,
                    },
                )
        else:
            props = {"where": "digest_send", "kind": "send_failed"}
            await events.track(session, digest.user_id, "error", props)
        await session.commit()


async def _deliver(
    candidate: _Candidate, digest: OutgoingDigest, max_client: MaxClient, now: datetime
) -> None:
    notification_id = await _claim(candidate, now, status="pending")
    if notification_id is None:
        return  # за эту неделю уже забрано
    try:
        await max_client.send_message(
            digest.text, user_id=digest.user_id, attachments=digest.attachments, fmt=None
        )
        ok = True
    except Exception:
        log.exception("сводка пользователю %s не отправлена", digest.user_id)
        ok = False
    try:
        await _record(digest, notification_id, ok=ok, now=now)
    except Exception:
        # Строка осталась pending — повторной сводки за эту неделю всё равно не будет.
        log.exception("не записан результат сводки %s", notification_id)


async def tick(now: datetime, max_client: MaxClient, *, reference: Reference | None = None) -> None:
    """Один проход: всем, кому пора, — сводка на неделю. Зовёт reminders.tick.

    Справочник читается, только когда есть кому слать. Ошибка по одному пользователю
    не мешает остальным.
    """
    now = as_utc(now)
    candidates = await _candidates(now)
    if not candidates:
        return
    reference = reference if reference is not None else loader.get_reference()
    for candidate in candidates:
        try:
            digest = await _prepare(candidate, reference)
            if digest is None:
                # Пустая неделя: отметка «решено, не отправляем», событий аналитики нет.
                await _claim(candidate, now, status="cancelled")
            else:
                await _deliver(candidate, digest, max_client, now)
        except Exception:
            log.exception("сводка пользователю %s: ошибка", candidate.user_id)
