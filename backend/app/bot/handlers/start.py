"""Пример сценария: приветствие → меню → вопрос с ответом текстом (FSM).
Заменяется реальным сценарием после выбора идеи."""

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.router import router
from app.core.texts import t


def main_menu() -> dict:
    return kb.inline_keyboard(
        [kb.callback(t("menu.ask_name"), "menu:ask_name")],
        [kb.callback(t("menu.about"), "menu:about")],
    )


@router.on("bot_started")
async def on_bot_started(ctx: Ctx) -> None:
    await ctx.track("bot_started", {"payload": ctx.payload})
    await ctx.set_state(None, {})
    await ctx.reply(t("start.greeting"), attachments=[main_menu()])


@router.on_text("/start")
async def on_start_command(ctx: Ctx) -> None:
    await on_bot_started(ctx)


@router.on_callback("menu:about")
async def on_about(ctx: Ctx) -> None:
    await ctx.reply(t("start.about"), attachments=[main_menu()])


@router.on_callback("menu:ask_name")
async def on_ask_name(ctx: Ctx) -> None:
    await ctx.set_state("ask_name")
    await ctx.track("scenario_step", {"step": "ask_name"})
    await ctx.reply(t("start.ask_name"))


@router.on_state("ask_name")
async def on_name_received(ctx: Ctx) -> None:
    name = (ctx.text or "").strip()
    if not name:
        await ctx.reply(t("errors.empty_text"))
        return
    await ctx.set_state(None, {"name": name})
    await ctx.track("scenario_completed", {"scenario": "ask_name"})
    await ctx.reply(t("start.nice_to_meet", name=name), attachments=[main_menu()])
