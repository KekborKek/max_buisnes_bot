"""Экран 12: кнопка «Без сводки» (`digest:off`) под сводкой в понедельник.

Саму сводку отправляет планировщик: app/calendar/digest.py. Кнопка выключает её навсегда:
`Profile.reminders["digest"] = False` (остальные настройки напоминаний не трогаем); вернуть —
на экране 13. Повторное нажатие ничего не меняет и отвечает тем же `digest.off_ok`.
"""

from app.bot.context import Ctx
from app.bot.router import router
from app.calendar import digest
from app.core.models import Profile
from app.core.texts import t


@router.on_callback(digest.OFF_PAYLOAD)
async def digest_off(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    await ctx.track("reminder_clicked", {"kind": digest.KIND, "action": "off"})
    profile = await ctx.session.get(Profile, ctx.user_id)
    if profile is not None and digest.digest_enabled(profile.reminders):
        # Новый словарь, а не правка на месте: изменения внутри JSON-колонки SQLAlchemy не видит.
        profile.reminders = {**(profile.reminders or {}), "digest": False}
    await ctx.reply(t("digest.off_ok"))
