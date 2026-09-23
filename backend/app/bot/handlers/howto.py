"""Экран 7 «Как сделать» и кнопка «Неверный срок» (docs/screens/07-howto.md, D4, D28).

Payload кнопок:
- `r:howto:<item_type>:<item_id>` — «Как сделать» из напоминания (экран 6);
- `h:done:<item_type>:<item_id>` — «Отметить выполненным», обрабатывает экран 8 (done.py);
- `h:wrong:obligation:<item_id>` — «Неверный срок».

У своей задачи кнопки «Как сделать» нет — экран для неё недостижим.
"""

import logging
from datetime import date

from sqlalchemy.exc import SQLAlchemyError

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.formatting import format_date
from app.bot.handlers import common, done
from app.bot.handlers.reminders import parse_payload
from app.bot.router import router
from app.calendar import loader, marks
from app.calendar import reminders as rem
from app.calendar.howto_text import expand_howto_steps
from app.core.models import Profile, UserObligation
from app.core.texts import t

log = logging.getLogger(__name__)

HOWTO = "r:howto:"
WRONG = "h:wrong:"

_STEPS = 3  # D4: ровно три шага


def howto_message(
    item: marks.OwnItem, today: date, *, with_wrong: bool = True
) -> tuple[str, list[dict]]:
    """Текст и кнопки экрана 7. `with_wrong=False` — после «Неверный срок» (кнопка исчезает)."""
    ob = item.obligation
    assert ob is not None
    day = format_date(item.due_date, today)
    steps = expand_howto_steps(ob, item.due_date, today)
    if len(steps) == _STEPS and all(step.strip() for step in steps):
        text = t(
            "howto.body",
            title=item.title,
            date=day,
            step1=steps[0],
            step2=steps[1],
            step3=steps[2],
            norm=ob.norm,
            last_checked=ob.last_checked_at.strftime("%d.%m.%Y"),
        )
        link = kb.link(ob.howto_link.label, ob.howto_link.url)
    else:
        # Быть не должно (загрузчик требует три шага), но защищаемся — экран 7, «Состояния».
        text = t("howto.no_steps", date=day, norm=ob.norm)
        link = kb.link(t("howto.btn_source"), ob.source_url)
    actions = [
        kb.callback(
            t("howto.btn_done"), done.payload(done.DONE_FROM_HOWTO, "obligation", item.item_id)
        )
    ]
    if with_wrong:
        actions.append(kb.callback(t("howto.btn_wrong"), f"{WRONG}obligation:{item.item_id}"))
    return text, [kb.inline_keyboard([link], actions)]


@router.on_callback(HOWTO)
async def on_howto(ctx: Ctx) -> None:
    """«Как сделать» из напоминания: три шага, норма, дата сверки, ссылка и кнопки."""
    parsed = parse_payload(ctx.payload)
    if parsed is None or ctx.user_id is None:
        log.warning("howto: непонятный payload %r", ctx.payload)
        return
    item_type, item_id = parsed
    now = common.now()
    profile = await ctx.session.get(Profile, ctx.user_id)
    today, tz = rem.user_today(profile, now)
    item = await marks.load_own_item(
        ctx.session,
        loader.get_reference(),
        user_id=ctx.user_id,
        item_type=item_type,
        item_id=item_id,
    )
    kind = await done.reminder_kind(ctx, item_type, item_id)
    await ctx.track("reminder_clicked", {"kind": kind, "action": "howto"})
    if item is None or item.obligation is None:
        # Чужое, удалённое или своя задача (у неё «Как сделать» нет) — показать нечего.
        log.info("howto: событие %s:%s недоступно для %s", item_type, item_id, ctx.user_id)
        return
    if item.done_at is not None:
        # Экран 6, «Состояния»: нажатие по отмеченному событию — экран 8, «уже отмечено».
        await ctx.reply(done.already_text(item, tz, today))
        return

    await ctx.track(
        "howto_opened",
        {"item_id": item.item_id, "item_type": item.item_type, "source": "reminder"},
    )
    text, attachments = howto_message(item, today)
    await ctx.reply(text, attachments=attachments)


@router.on_callback(WRONG)
async def on_wrong(ctx: Ctx) -> None:
    """«Неверный срок»: запись `WrongDateReport` (одна), ответ `wrong_thanks`, кнопка исчезает.

    Порядок как у snooze (handlers/reminders.py): чтение → правка сообщения в MAX (сеть — до
    первой записи) → запись.
    """
    parsed = parse_payload(ctx.payload)
    if parsed is None or ctx.user_id is None or parsed[0] != "obligation":
        log.warning("wrong: непонятный payload %r", ctx.payload)
        return
    _, item_id = parsed
    now = common.now()
    profile = await ctx.session.get(Profile, ctx.user_id)
    today, _ = rem.user_today(profile, now)
    item = await marks.load_own_item(
        ctx.session,
        loader.get_reference(),
        user_id=ctx.user_id,
        item_type="obligation",
        item_id=item_id,
    )
    if item is None:
        log.info("wrong: обязательство %s недоступно для %s", item_id, ctx.user_id)
        return

    await _remove_wrong_button(ctx, item, today)

    assert isinstance(item.row, UserObligation)
    try:
        created = await marks.report_wrong_date(
            ctx.session, user_id=ctx.user_id, uo=item.row, now=now
        )
        if created:
            await ctx.track(
                "wrong_date_reported",
                {"item_id": item.item_id, "item_type": item.item_type, "source": "howto"},
            )
    except SQLAlchemyError:
        await done.reply_error(ctx, "wrong_date_reported", ctx.payload or "")
        return
    await ctx.reply(t("howto.wrong_thanks"))


async def _remove_wrong_button(ctx: Ctx, item: marks.OwnItem, today: date) -> None:
    """Перерисовывает сообщение экрана 7 без «Неверный срок» тем же рендером, что и показ."""
    mid = (((ctx.update.get("message") or {}).get("body")) or {}).get("mid")
    if not mid:
        log.warning("wrong: в апдейте нет message.body.mid — кнопку не убираем")
        return
    text, attachments = howto_message(item, today, with_wrong=False)
    try:
        await ctx.max.edit_message(mid, text, attachments=attachments)
    except Exception:
        log.exception("wrong: не удалось убрать кнопку из сообщения %s", mid)
