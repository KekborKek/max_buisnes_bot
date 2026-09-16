from app.bot.context import Ctx
from app.bot.handlers.start import main_menu
from app.bot.router import router
from app.core.texts import t


@router.fallback
async def on_unknown(ctx: Ctx) -> None:
    if ctx.update_type not in ("message_created", "message_callback"):
        return  # служебные события молча игнорируем
    if ctx.callback_id:
        await ctx.max.answer_callback(ctx.callback_id)
    await ctx.reply(t("errors.unknown"), attachments=[main_menu()])
