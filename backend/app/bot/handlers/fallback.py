"""Непонятое действие. Экран 10 делает T8b; до него — `errors.unknown` + кнопки экрана 1."""

from app.bot.context import Ctx
from app.bot.handlers.start import start_keyboard
from app.bot.router import router
from app.core.texts import t


@router.fallback
async def on_unknown(ctx: Ctx) -> None:
    if ctx.update_type not in ("message_created", "message_callback"):
        return  # служебные события со своими обработчиками — в handlers/service.py
    await ctx.reply(t("errors.unknown"), attachments=[start_keyboard()])
