"""Экран 8: ответ на «Отметить выполненным» и «Отменить отметку» (docs/screens/08-done.md).

Payload кнопок:
- `r:done:<item_type>:<item_id>` — из напоминания (экран 6), `source = reminder`;
- `h:done:<item_type>:<item_id>` — из «Как сделать» (экран 7), `source = howto`;
- `d:undo:<item_type>:<item_id>` — «Отменить отметку» под ответом экрана 8.

Доменная часть (условный UPDATE, отмена и возврат уведомлений) — app/calendar/marks.py.
"""

import logging
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.formatting import format_date
from app.bot.handlers import common
from app.bot.handlers.calendar_ready import lower_first
from app.bot.handlers.reminders import parse_payload
from app.bot.router import router
from app.calendar import loader, marks
from app.calendar import reminders as rem
from app.calendar.reminders import as_utc
from app.calendar.types import Reference
from app.core.config import get_settings
from app.core.models import Notification, Profile, Task, UserObligation
from app.core.texts import t

log = logging.getLogger(__name__)

DONE_FROM_REMINDER = "r:done:"
DONE_FROM_HOWTO = "h:done:"
UNDO = "d:undo:"

_SOURCES = {DONE_FROM_REMINDER: "reminder", DONE_FROM_HOWTO: "howto"}
# Вид напоминания для reminder_clicked, если по событию не нашлось отправленного.
UNKNOWN_KIND = "unknown"


def payload(prefix: str, item_type: str, item_id: int) -> str:
    return f"{prefix}{item_type}:{item_id}"


async def reminder_kind(ctx: Ctx, item_type: str, item_id: int) -> str:
    """`kind` для reminder_clicked: в payload кнопки его нет, берём последнее отправленное.

    Кнопки «Отметить» и «Как сделать» есть только в одиночных напоминаниях, поэтому
    последнее `sent` по событию — то сообщение, в котором нажали (или более свежее).
    """
    kind = await ctx.session.scalar(
        select(Notification.kind)
        .where(
            Notification.item_type == item_type,
            Notification.item_id == item_id,
            Notification.status == "sent",
        )
        .order_by(Notification.send_at.desc(), Notification.id.desc())
        .limit(1)
    )
    return kind or UNKNOWN_KIND


def already_text(item: marks.OwnItem, tz: str, today: date) -> str:
    """`done.already` с датой отметки в поясе пользователя."""
    assert item.done_at is not None
    done_day = as_utc(item.done_at).astimezone(ZoneInfo(tz)).date()
    return t("done.already", done_date=format_date(done_day, today))


async def _next_item(ctx: Ctx, reference: Reference, today: date) -> tuple[date, str] | None:
    """Следующее по дате неотмеченное событие (обязательство или своя задача) с due_date ≥ сегодня.

    Дата — из БД, где она вычислена по справочнику (D15). Обязательство, которого уже нет
    в справочнике, пропускаем — пересборка его удалит.
    """
    assert ctx.user_id is not None
    titles = {ob.id: ob.title for ob in reference.catalog.obligations}
    uos = await ctx.session.scalars(
        select(UserObligation)
        .where(
            UserObligation.user_id == ctx.user_id,
            UserObligation.done_at.is_(None),
            UserObligation.due_date >= today,
        )
        .order_by(UserObligation.due_date, UserObligation.id)
    )
    candidates = [
        (uo.due_date, 0, uo.id, titles[uo.obligation_id])
        for uo in uos
        if uo.obligation_id in titles
    ]
    task = await ctx.session.scalar(
        select(Task)
        .where(
            Task.user_id == ctx.user_id,
            Task.done_at.is_(None),
            Task.deleted_at.is_(None),
            Task.due_date >= today,
        )
        .order_by(Task.due_date, Task.id)
        .limit(1)
    )
    if task is not None:
        candidates.append((task.due_date, 1, task.id, task.title))
    if not candidates:
        return None
    due, _, _, title = min(candidates)
    return due, title


def done_text(item: marks.OwnItem, nxt: tuple[date, str] | None, today: date) -> str:
    """Ответ экрана 8: `body`, `last_in_year` или `nothing_next`."""
    fields = {"title": item.title, "disclaimer": t("disclaimer.mark")}
    if nxt is None:
        return t("done.nothing_next", **fields)
    next_date, next_title = nxt
    fields |= {"next_date": format_date(next_date, today), "next_title": lower_first(next_title)}
    if next_date.year > today.year:
        return t("done.last_in_year", year=today.year, **fields)
    return t("done.body", **fields)


def done_keyboard(item: marks.OwnItem) -> list[dict]:
    """«Открыть календарь» (экран 14; без MAX_BOT_USERNAME не показываем) и «Отменить отметку»."""
    row = []
    if get_settings().max_bot_username:
        row.append(kb.open_app(t("done.btn_open")))
    row.append(kb.callback(t("done.btn_undo"), payload(UNDO, item.item_type, item.item_id)))
    return [kb.inline_keyboard(row)]


async def reply_error(ctx: Ctx, where: str, retry_payload: str) -> None:
    """«Отметка не сохранилась»: откат, common.error + «Повторить» с тем же действием."""
    log.exception("%s: запись не удалась", where)
    await ctx.session.rollback()
    await ctx.track("error", {"where": where, "kind": "db"})
    await ctx.reply(
        t("common.error"),
        attachments=[kb.inline_keyboard([kb.callback(t("common.btn_retry"), retry_payload)])],
    )


@router.on_callback(DONE_FROM_REMINDER)
@router.on_callback(DONE_FROM_HOWTO)
async def on_done(ctx: Ctx) -> None:
    """Отметка: условный UPDATE + отмена уведомлений → экран 8 или «уже отмечено».

    Сети нет (ответ — через outbox), поэтому порядок чтение/запись здесь не важен.
    """
    parsed = parse_payload(ctx.payload)
    if parsed is None or ctx.user_id is None:
        log.warning("done: непонятный payload %r", ctx.payload)
        return
    item_type, item_id = parsed
    raw = ctx.payload or ""
    prefix = DONE_FROM_REMINDER if raw.startswith(DONE_FROM_REMINDER) else DONE_FROM_HOWTO
    now = common.now()
    reference = loader.get_reference()
    profile = await ctx.session.get(Profile, ctx.user_id)
    today, tz = rem.user_today(profile, now)
    item = await marks.load_own_item(
        ctx.session, reference, user_id=ctx.user_id, item_type=item_type, item_id=item_id
    )
    if prefix == DONE_FROM_REMINDER:
        kind = await reminder_kind(ctx, item_type, item_id)
        await ctx.track("reminder_clicked", {"kind": kind, "action": "done"})
    if item is None:
        log.info("done: событие %s:%s не найдено у %s", item_type, item_id, ctx.user_id)
        return

    try:
        changed = await marks.mark_done(ctx.session, item, now=now)
        if not changed:
            await ctx.reply(already_text(item, tz, today))
            return
        await ctx.track(
            "item_done",
            {
                "item_id": item.item_id,
                "item_type": item.item_type,
                "source": _SOURCES[prefix],
                "days_before_deadline": (item.due_date - today).days,  # D19: может быть < 0
            },
        )
        nxt = await _next_item(ctx, reference, today)
    except SQLAlchemyError:
        await reply_error(ctx, "item_done", raw)
        return
    await ctx.reply(done_text(item, nxt, today), attachments=done_keyboard(item))


@router.on_callback(UNDO)
async def on_undo(ctx: Ctx) -> None:
    """«Отменить отметку»: сообщение → `done.undone` без кнопок, отметка снимается.

    Порядок как у snooze (handlers/reminders.py): чтение → правка сообщения в MAX (сеть — до
    первой записи) → запись. Повторное нажатие правит сообщение так же и ничего не пишет.
    """
    parsed = parse_payload(ctx.payload)
    if parsed is None or ctx.user_id is None:
        log.warning("undo: непонятный payload %r", ctx.payload)
        return
    item_type, item_id = parsed
    now = common.now()
    profile = await ctx.session.get(Profile, ctx.user_id)
    item = await marks.load_own_item(
        ctx.session,
        loader.get_reference(),
        user_id=ctx.user_id,
        item_type=item_type,
        item_id=item_id,
    )
    if item is None:
        log.info("undo: событие %s:%s не найдено у %s", item_type, item_id, ctx.user_id)
        return

    edited = await _edit_to_undone(ctx)

    try:
        if await marks.unmark_done(ctx.session, item, profile=profile, now=now):
            await ctx.track(
                "item_undone",
                {"item_id": item.item_id, "item_type": item.item_type, "source": "bot"},
            )
    except SQLAlchemyError:
        await reply_error(ctx, "item_undone", ctx.payload or "")
        return
    if not edited:
        await ctx.reply(t("done.undone"))


async def _edit_to_undone(ctx: Ctx) -> bool:
    """Меняет ответ экрана 8 на `done.undone` без кнопок. False — править нечего или не вышло."""
    mid = (((ctx.update.get("message") or {}).get("body")) or {}).get("mid")
    if not mid:
        log.warning("undo: в апдейте нет message.body.mid — отвечаем новым сообщением")
        return False
    try:
        await ctx.max.edit_message(mid, t("done.undone"), attachments=[])
    except Exception:
        log.exception("undo: не удалось изменить сообщение %s", mid)
        return False
    return True
