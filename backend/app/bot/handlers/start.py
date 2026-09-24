"""Экран 1 «Первое сообщение» (docs/screens/01-start.md): /start и переход по ссылке.

Payload кнопок: start:check — «Проверить НДС», start:rebuild — «Собрать заново»,
start:retry — «Повторить» после ошибки справочника, about:open — «О сервисе»
(обработчик экрана 11 делает T9; до него кнопка уходит в fallback).

Диплинк `?start=profile_edit` — «Изменить» на экране 19 мини-аппа (WebApp.openMaxLink): у кого
календарь собран, сразу вопросы экрана 2, как по «Собрать заново». Придёт ли bot_started
в уже начатый диалог — [сверить] на живом клиенте (dev.max.ru об этом молчит).
"""

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.handlers import onboarding
from app.bot.handlers.common import ensure_profile, nds_limit_text, reply_reference_error
from app.bot.router import router
from app.calendar.types import ReferenceFileError
from app.core.config import get_settings
from app.core.texts import t

CHECK = "start:check"
REBUILD = "start:rebuild"
RETRY = "start:retry"
ABOUT = "about:open"
# Параметр диплинка из мини-аппа (miniapp/src/screens/ProfileScreen.tsx, EDIT_START_PAYLOAD).
PROFILE_EDIT = "profile_edit"


def start_keyboard() -> dict:
    """Один ряд: «Проверить НДС», «О сервисе»."""
    return kb.inline_keyboard(
        [kb.callback(t("start.btn_check"), CHECK), kb.callback(t("start.btn_about"), ABOUT)]
    )


def built_keyboard() -> dict:
    """«Открыть календарь» (open_app, D27) и «Собрать заново».

    Без MAX_BOT_USERNAME кнопку мини-аппа не показываем (keyboards.open_app).
    """
    row = []
    if get_settings().max_bot_username:
        row.append(kb.open_app(t("start.btn_open")))
    row.append(kb.callback(t("start.btn_rebuild"), REBUILD))
    return kb.inline_keyboard(row)


async def show_start(ctx: Ctx, *, start_param: str | None, track: bool = True) -> None:
    """Приветствие или «календарь уже собран».

    Шаг онбординга сбрасывается (текст после приветствия — не ответ на вопрос), а ответы
    и данные состояния остаются до следующего нажатия «Проверить НДС».
    """
    if ctx.user_id is None:
        return
    if track:
        await ctx.track("bot_started", {"start_param": start_param})
    profile = await ensure_profile(ctx)
    _, data = await ctx.get_state()
    await ctx.set_state(None, data)

    if profile.calendar_built_at is not None:
        await ctx.reply(t("start.already_built"), attachments=[built_keyboard()])
        return
    try:
        nds_limit = nds_limit_text(profile)
    except ReferenceFileError as exc:
        await reply_reference_error(ctx, exc, where="start", retry_payload=RETRY)
        return
    await ctx.reply(t("start.greeting", nds_limit=nds_limit), attachments=[start_keyboard()])


@router.on("bot_started")
async def on_bot_started(ctx: Ctx) -> None:
    if ctx.payload == PROFILE_EDIT and ctx.user_id is not None:
        profile = await ensure_profile(ctx)
        if profile.calendar_built_at is not None:
            # «Изменить» из экрана 19: ответы и календарь остаются до перезаписи (onboarding.begin).
            await ctx.track("bot_started", {"start_param": ctx.payload})
            await onboarding.begin(ctx)
            return
    await show_start(ctx, start_param=ctx.payload)


@router.on_text("/start")
async def on_start_command(ctx: Ctx) -> None:
    await show_start(ctx, start_param=None)


@router.on_callback(RETRY)
async def on_start_retry(ctx: Ctx) -> None:
    """«Повторить» — не новый /start: bot_started второй раз не пишем."""
    await show_start(ctx, start_param=None, track=False)


@router.on_callback(CHECK)
async def on_check(ctx: Ctx) -> None:
    await onboarding.begin(ctx)


@router.on_callback(REBUILD)
async def on_rebuild(ctx: Ctx) -> None:
    await onboarding.begin(ctx)
