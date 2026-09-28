"""«Написать нам» — обращение прямо в боте (экраны 10 и 11, D13).

Вход: кнопка «Написать нам» на экране 11 (`feedback:start:about`), на экране 10 после трёх
непонятых сообщений (`feedback:start:fallback`) и команда `/feedback`. Бот просит написать
одним текстом и ждёт в состоянии `feedback_wait`:
    текст до `MAX_LENGTH` символов — сохраняем `Feedback`, подтверждаем, выходим из ожидания;
    длиннее — просим сократить и ждём дальше (обрезать молча не стали: потерялся бы конец);
    не текст или пустой — просим ещё раз;
    «Отмена» (`feedback:cancel`) — выходим без сохранения;
    любое другое действие (команда, другая кнопка) работает как обычно и снимает ожидание —
    это делает `after_handler`, его зовёт диспетчер.
`/feedback <текст>` сохраняет обращение сразу, без вопроса.

Пересылка команде — всем из `ADMIN_IDS` личным сообщением, через `ctx.defer`: после коммита
и после ответа пользователю, сеть вне транзакции. Ошибка пересылки ответ не ломает — лог
и `forwarded=False`; обращение лежит в базе, его видно в `/feedback_list`. Без `ADMIN_IDS` —
только сохранение и предупреждение в лог.

`/feedback_list` — последние обращения, только для `ADMIN_IDS` (как `/demo_remind`); для
остальных команды как будто нет — ответ экрана 10.

`SUPPORT_URL` бот больше не использует: кнопка — callback и показывается всегда.

Повторная доставка того же апдейта отсекается ключом в `processed_updates` (диспетчер),
поэтому второго обращения не будет; к тому же после сохранения ожидание уже снято.
"""

import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.handlers import fallback
from app.bot.handlers.common import as_utc, ensure_profile
from app.bot.router import router
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.max_client import MaxClient
from app.core.models import DEFAULT_TIMEZONE, DialogState, Feedback, User
from app.core.texts import t

log = logging.getLogger(__name__)

STATE = "feedback_wait"
START = "feedback:start:"  # + источник: about | fallback
CANCEL = "feedback:cancel"
COMMAND = "/feedback"
LIST_COMMAND = "/feedback_list"
SOURCES = frozenset({"about", "fallback", "command"})

MAX_LENGTH = 2000  # сообщение MAX — до 4000 символов; с заголовком пересылки влезает с запасом
LIST_LIMIT = 10
LIST_PREVIEW = 100  # символов текста обращения в /feedback_list
_KEEP = "feedback_keep_waiting"  # ctx.extra: апдейт обработал сам экран — ожидание не снимать


def write_button(text_key: str, source: str) -> dict:
    """Кнопка «Написать нам» для экрана `source` (about | fallback)."""
    return kb.callback(t(text_key), f"{START}{source}")


def _cancel_keyboard() -> dict:
    return kb.inline_keyboard([kb.callback(t("feedback.btn_cancel"), CANCEL)])


def _author(name: str | None, user_id: int) -> str:
    if name:
        return t("feedback.author", name=name, user_id=user_id)
    return t("feedback.author_id", user_id=user_id)


# --- вход и ожидание ---------------------------------------------------------------------


async def _ask(ctx: Ctx, text_key: str = "feedback.prompt", **kwargs: object) -> None:
    """Просьба написать (или переписать) обращение; остаёмся в ожидании."""
    _, data = await ctx.get_state()
    await ctx.set_state(STATE, data)
    ctx.extra[_KEEP] = True
    await ctx.reply(t(text_key, **kwargs), attachments=[_cancel_keyboard()])


async def start(ctx: Ctx, source: str) -> None:
    if ctx.user_id is None:
        return
    await ctx.track("feedback_started", {"source": source})
    await _ask(ctx)


@router.on_callback(START)
async def on_start_button(ctx: Ctx) -> None:
    source = (ctx.payload or "")[len(START) :]
    await start(ctx, source if source in SOURCES else "about")


@router.on_command(COMMAND)
async def on_feedback_command(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    parts = (ctx.text or "").strip().split(maxsplit=1)
    if len(parts) > 1:
        await ctx.track("feedback_started", {"source": "command"})
        await _accept(ctx, parts[1])
        return
    await start(ctx, "command")


@router.on_callback(CANCEL)
async def on_cancel(ctx: Ctx) -> None:
    """«Отмена»: без сохранения. Кнопка из старого сообщения (ожидания уже нет) — то же."""
    if ctx.user_id is None:
        return
    state, data = await ctx.get_state()
    if state == STATE:
        await ctx.set_state(None, data)
    await ctx.track("feedback_cancelled", {})
    await ctx.reply(t("feedback.cancelled"))


@router.on_state(STATE)
async def on_text_while_waiting(ctx: Ctx) -> None:
    await _accept(ctx, ctx.text)


async def _accept(ctx: Ctx, raw: str | None) -> None:
    """Текст обращения: проверить, сохранить, подтвердить, поставить пересылку."""
    assert ctx.user_id is not None
    text = (raw or "").strip()
    if not text:
        await _ask(ctx, "feedback.empty")
        return
    if len(text) > MAX_LENGTH:
        await _ask(ctx, "feedback.too_long", limit=MAX_LENGTH, length=len(text))
        return

    await ensure_profile(ctx)  # Feedback.user_id ссылается на users
    item = Feedback(user_id=ctx.user_id, text=text)
    ctx.session.add(item)
    await ctx.session.flush()  # нужен id для «Обращение #id»

    state, data = await ctx.get_state()
    if state == STATE:
        await ctx.set_state(None, data)
    await ctx.track("feedback_sent", {"length": len(text)})
    await ctx.reply(t("feedback.sent"))

    admin_ids = list(get_settings().admin_ids)
    if not admin_ids:
        log.warning("обращение #%s сохранено, но ADMIN_IDS пуст — пересылать некому", item.id)
        return
    message = t("feedback.forward", id=item.id, author=_author(ctx.user_name, ctx.user_id), text=text)
    feedback_id = item.id
    client = ctx.max
    ctx.defer(lambda: forward(client, feedback_id, message, admin_ids))


async def forward(client: MaxClient, feedback_id: int, message: str, admin_ids: list[int]) -> None:
    """Пересылка админам после коммита. Дошло хотя бы одному — `forwarded=True`.

    Без форматирования (`fmt=None`): текст пользователя не должен превращаться в разметку.
    """
    delivered = 0
    for admin_id in admin_ids:
        try:
            await client.send_message(message, user_id=admin_id, fmt=None)
            delivered += 1
        except Exception:
            log.exception("не удалось переслать обращение #%s админу %s", feedback_id, admin_id)
    if not delivered:
        log.error("обращение #%s не переслано никому, forwarded=False", feedback_id)
        return
    async with SessionLocal() as session:
        item = await session.get(Feedback, feedback_id)
        if item is not None:
            item.forwarded = True
            await session.commit()


async def after_handler(ctx: Ctx) -> None:
    """Любое действие, кроме ответа на просьбу, снимает ожидание. Зовёт диспетчер до коммита."""
    if ctx.user_id is None or ctx.extra.get(_KEEP):
        return
    row = await ctx.session.get(DialogState, ctx.user_id)
    if row is not None and row.state == STATE:
        row.state = None


# --- /feedback_list ----------------------------------------------------------------------


@router.on_command(LIST_COMMAND)
async def on_feedback_list(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    if ctx.user_id not in get_settings().admin_ids:
        await fallback.show_unknown(ctx)  # для обычного пользователя команды как будто нет
        return

    rows = (
        await ctx.session.execute(
            select(Feedback, User.name)
            .join(User, User.user_id == Feedback.user_id, isouter=True)
            .order_by(Feedback.id.desc())
            .limit(LIST_LIMIT)
        )
    ).all()
    if not rows:
        await ctx.reply(t("feedback.list_empty"))
        return

    tz = ZoneInfo(DEFAULT_TIMEZONE)
    lines = [t("feedback.list_title", count=len(rows))]
    for item, name in rows:
        preview = item.text if len(item.text) <= LIST_PREVIEW else f"{item.text[:LIST_PREVIEW]}…"
        lines.append(
            t(
                "feedback.list_item",
                id=item.id,
                when=as_utc(item.created_at).astimezone(tz).strftime("%d.%m %H:%M"),
                author=_author(name, item.user_id),
                status="" if item.forwarded else t("feedback.list_not_forwarded"),
                text=preview.replace("\n", " "),
            )
        )
    await ctx.reply("\n".join(lines))
