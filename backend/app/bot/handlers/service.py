"""Служебные события MAX: бота остановили, добавили в чат, удалили из чата.

Сообщений пользователю не отправляем: это не реплика в диалоге, а изменение статуса бота —
ответ в этот момент выглядел бы навязчиво, а на `bot_removed` отправлять уже некуда.
Смысл обработчиков — аналитика: `bot_stopped` это отток, и без него воронка не считается.

Структуры апдётов взяты из документации (chat_id, user, is_channel) — **[сверить]**
на живых апдейтах, см. `docs/max-api-notes.md`.
"""

from app.bot.context import Ctx
from app.bot.router import router


def _props(ctx: Ctx) -> dict:
    return {"chat_id": ctx.chat_id, "is_channel": ctx.is_channel}


@router.on("bot_stopped")
async def on_bot_stopped(ctx: Ctx) -> None:
    """Пользователь остановил бота — метрика оттока."""
    await ctx.track("bot_stopped", _props(ctx))


@router.on("bot_added")
async def on_bot_added(ctx: Ctx) -> None:
    """Бота добавили в чат или канал."""
    await ctx.track("bot_added", _props(ctx))


@router.on("bot_removed")
async def on_bot_removed(ctx: Ctx) -> None:
    """Бота удалили из чата или канала — отток в групповом сценарии."""
    await ctx.track("bot_removed", _props(ctx))
