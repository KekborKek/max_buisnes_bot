"""Экран 10 «Бот не понял или сервис не работает» (docs/screens/10-fallback.md).

Три случая:
    unknown — ни даты, ни похожего на задачу текста (и непонятная кнопка): `show_unknown`;
    no_date — похоже на задачу, даты нет: `show_no_date` (вход — из task_chat);
    service — исключение в обработчике: `on_failure`, его зовёт диспетчер после rollback.

Состояние — в `DialogState.data`:
    fb_unknown  — непонятых сообщений подряд (с третьего — `unknown_3` и «Написать нам»);
    fb_service  — сбоев подряд (со второго — `service_2`);
    last_action — {update_type, text, payload} упавшего действия, его повторяет «Повторить»;
    task_title  — название задачи без даты, ждёт «Завтра» / «Через неделю» (экран 9);
    task_draft  — {title, due_date[, id][, remind_hour, remind_minute]}: черновик для формы 17
                  («Выбрать дату», D27); `id` — у каждого нового черновика (экраны 9 и 10, #87,
                  #96), его несут кнопки; время — только если оно было в сообщении (#103).
Счётчики сбрасывает любое успешное действие (`after_handler`, зовёт диспетчер до коммита).

Payload кнопок: fb:retry — «Повторить»; fb:tomorrow:<id> и fb:week:<id> обрабатывает task_chat
(`<id>` — id черновика «не нашёл дату», #96). «Выбрать дату» — open_app с `task_draft_<id>`:
`/api/me` отдаст черновик, только если id совпал с текущим, иначе форма 17 пустая (#96).
"""

import logging
import secrets
from datetime import date, time, timedelta

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.handlers import feedback
from app.bot.handlers.start import ABOUT
from app.bot.router import router
from app.core.config import get_settings
from app.core.models import DialogState
from app.core.texts import t

log = logging.getLogger(__name__)

RETRY = "fb:retry"
TOMORROW = "fb:tomorrow"
WEEK = "fb:week"
# start_param мини-аппа: форма 17 с черновиком (D27). С id черновика — «task_draft_<id>» (#96);
# голый «task_draft» — кнопки, отправленные до #96. Формат читают api/routes и miniapp/router.
DRAFT_START_PARAM = "task_draft"

UNKNOWN_KEY = "fb_unknown"
SERVICE_KEY = "fb_service"
LAST_ACTION_KEY = "last_action"
TASK_TITLE_KEY = "task_title"
TASK_DRAFT_KEY = "task_draft"

UNKNOWN_LIMIT = 3  # «Три непонятых сообщения подряд»
SERVICE_LIMIT = 2  # «Два сбоя подряд»
_COUNTERS = (UNKNOWN_KEY, SERVICE_KEY)
# Какие действия повторяет «Повторить»: то, что сделал сам пользователь.
_USER_ACTIONS = frozenset({"message_created", "message_callback", "bot_started"})
# ctx.extra: счётчик, который этот апдейт только что увеличил, — его after_handler не сбрасывает.
_KEEP = "fb_keep_counter"


def new_draft_id() -> str:
    """Короткий id черновика (8 символов 0-9a-f): его несут кнопки сообщения (#87, #96)."""
    return secrets.token_hex(4)


def draft_start_param(draft_id: str | None) -> str:
    """start_param «Изменить» / «Выбрать дату»: «task_draft_<id>»; черновик без id — «task_draft».

    Ограничений длины и алфавита payload `open_app` схема MAX не задаёт (docs/max-api-notes.md);
    держимся латиницы, цифр и `_`, как `item_<type>_<id>`.
    """
    return f"{DRAFT_START_PARAM}_{draft_id}" if draft_id else DRAFT_START_PARAM


def draft(
    title: str, due_date: date, draft_id: str | None = None, remind_time: time | None = None
) -> dict:
    """`task_draft` в том виде, что читает `/api/me` (api/routes.task_draft): title, due_date.

    `id` — новое поле (#87): его несут кнопки экранов 9 и 10, чтобы старая кнопка не взяла
    чужой черновик. `/api/me` сверяет его с id из start_param (#96); title и due_date
    переименовывать нельзя. `remind_hour` и `remind_minute` — время из сообщения (#103),
    пишутся только вместе и только когда время было.
    """
    value: dict = {"title": title, "due_date": due_date.isoformat()}
    if draft_id is not None:
        value["id"] = draft_id
    if remind_time is not None:
        value["remind_hour"] = remind_time.hour
        value["remind_minute"] = remind_time.minute
    return value


def open_calendar_button(text_key: str, start_param: str | None = None) -> dict | None:
    """Кнопка мини-аппа (D27); без MAX_BOT_USERNAME её не показываем (keyboards.open_app)."""
    if not get_settings().max_bot_username:
        return None
    return kb.open_app(t(text_key), payload=start_param)


def retry_keyboard() -> dict:
    return kb.inline_keyboard([kb.callback(t("fallback.btn_retry"), RETRY)])


# --- не понял ------------------------------------------------------------------------------


def unknown_keyboard(*, with_write: bool) -> dict:
    """«Открыть календарь» «О сервисе»; ниже — «Написать нам» (обращение в боте, D13)."""
    row = [kb.callback(t("fallback.btn_about"), ABOUT)]
    open_button = open_calendar_button("fallback.btn_open")
    if open_button is not None:
        row.insert(0, open_button)
    rows = [row]
    if with_write:
        rows.append([feedback.write_button("fallback.btn_write", "fallback")])
    return kb.inline_keyboard(*rows)


async def show_unknown(ctx: Ctx, *, text_key: str = "fallback.unknown") -> None:
    """Случай «не понял». С третьего раза подряд — `unknown_3` и «Написать нам» (D13)."""
    state, data = await ctx.get_state()
    count = int(data.get(UNKNOWN_KEY) or 0) + 1
    await ctx.set_state(state, {**data, UNKNOWN_KEY: count})
    ctx.extra[_KEEP] = UNKNOWN_KEY
    await ctx.track("fallback_shown", {"case": "unknown"})

    text = t(text_key)
    third = count >= UNKNOWN_LIMIT
    if third:
        text = f"{text}\n{t('fallback.unknown_3')}"
    await ctx.reply(text, attachments=[unknown_keyboard(with_write=third)])


# --- не нашёл дату -------------------------------------------------------------------------


def no_date_keyboard(draft_id: str) -> dict:
    """«Завтра» «Через неделю» / «Выбрать дату» (форма 17 с названием); все несут id черновика."""
    rows = [
        [
            kb.callback(t("fallback.btn_tomorrow"), f"{TOMORROW}:{draft_id}"),
            kb.callback(t("fallback.btn_week"), f"{WEEK}:{draft_id}"),
        ]
    ]
    pick = open_calendar_button("fallback.btn_pick", draft_start_param(draft_id))
    if pick is not None:
        rows.append([pick])
    return kb.inline_keyboard(*rows)


async def show_no_date(ctx: Ctx, title: str, today: date, remind_time: time | None = None) -> None:
    """Случай «не нашёл дату»: название ждёт даты в `task_title`, текст не теряется.

    «Выбрать дату» — open_app, нажатие до бота не доходит, поэтому черновик формы 17 пишем
    сразу: название и завтрашний день (форма всё равно даст выбрать дату). Новый id черновика
    делает кнопки прежних сообщений устаревшими (#96). Время из сообщения (#103) — в том же
    черновике: его берут «Завтра» / «Через неделю» и форма 17.
    """
    draft_id = new_draft_id()
    state, data = await ctx.get_state()
    data[TASK_TITLE_KEY] = title
    data[TASK_DRAFT_KEY] = draft(title, today + timedelta(days=1), draft_id, remind_time)
    await ctx.set_state(state, data)
    await ctx.track("date_not_parsed", {})
    await ctx.track("fallback_shown", {"case": "no_date"})
    await ctx.reply(t("fallback.no_date", title=title), attachments=[no_date_keyboard(draft_id)])


# --- сервис не ответил ---------------------------------------------------------------------


async def _record_failure(ctx: Ctx) -> int:
    """Счётчик сбоев, упавшее действие для «Повторить» и событие. Возвращает номер сбоя подряд."""
    if ctx.user_id is None:
        await ctx.track("fallback_shown", {"case": "service"})
        return 1
    state, data = await ctx.get_state()
    count = int(data.get(SERVICE_KEY) or 0) + 1
    data[SERVICE_KEY] = count
    # Упал сам «Повторить» до подмены действия — прежний last_action в базе и так остался.
    if ctx.update_type in _USER_ACTIONS and ctx.payload != RETRY:
        data[LAST_ACTION_KEY] = {
            "update_type": ctx.update_type,
            "text": ctx.text,
            "payload": ctx.payload,
        }
    await ctx.set_state(state, data)
    await ctx.track("fallback_shown", {"case": "service"})
    return count


async def on_failure(ctx: Ctx) -> None:
    """Случай «сервис не ответил». Зовёт диспетчер после rollback транзакции обработчика.

    Своя короткая транзакция без сетевых вызовов: счётчик, `last_action`, событие. Её сбой
    только логируется — ответ `service` + «Повторить» всё равно уходит (через outbox).
    """
    count = 1
    try:
        count = await _record_failure(ctx)
        await ctx.session.commit()
    except Exception:
        log.exception("не удалось записать сбой для %s", ctx.update_type)
        try:
            await ctx.session.rollback()
        except Exception:
            log.exception("rollback после записи сбоя не прошёл для %s", ctx.update_type)
    key = "fallback.service_2" if count >= SERVICE_LIMIT else "fallback.service"
    await ctx.reply(t(key), attachments=[retry_keyboard()])


async def after_handler(ctx: Ctx) -> None:
    """Успешное действие сбрасывает счётчики экрана 10. Зовёт диспетчер до коммита.

    Строку состояния не создаёт: служебным апдейтам (bot_stopped и т.п.) она не нужна.
    """
    if ctx.user_id is None:
        return
    row = await ctx.session.get(DialogState, ctx.user_id)
    if row is None or not row.data:
        return
    keep = ctx.extra.get(_KEEP)
    data = dict(row.data)
    stale = [key for key in _COUNTERS if key != keep and key in data]
    if stale:
        for key in stale:
            del data[key]
        row.data = data


@router.on_callback(RETRY)
async def on_retry(ctx: Ctx) -> None:
    """«Повторить»: то действие, что упало, — тем же обработчиком, что и в первый раз.

    Контекст подменяется сохранённым действием; после успешного повтора `last_action`
    стирается. Повтор снова упал — диспетчер откатит и это стирание.
    Нечего повторять (кнопка из старого сообщения) — случай «не понял», из него есть выход.
    """
    _, data = await ctx.get_state()
    action = data.get(LAST_ACTION_KEY)
    if not isinstance(action, dict) or action.get("update_type") not in _USER_ACTIONS:
        await show_unknown(ctx)
        return
    ctx.update_type = action["update_type"]
    ctx.text = action.get("text")
    ctx.payload = action.get("payload")
    handler = await router.resolve(ctx)
    if handler is None or handler is on_retry:
        await show_unknown(ctx)
        return
    await handler(ctx)
    state, data = await ctx.get_state()
    data.pop(LAST_ACTION_KEY, None)
    await ctx.set_state(state, data)
