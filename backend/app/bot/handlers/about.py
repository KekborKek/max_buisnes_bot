"""Экран 11 «О сервисе» (docs/screens/11-about.md): `/about` и кнопка `about:open` с экрана 1.

Экран статичен: загрузки, пустоты и своей ошибки у него нет (поправка планировщика к
issue #54 — раздел «Состояния» файла экрана важнее issue). Справочник недоступен — тот же
текст, но без даты сверки (`about.body_no_reference`), причина — в лог как warning.

Кнопки: «Проверить НДС» (календарь ещё не собран) или «Открыть календарь» (собран, и задан
`MAX_BOT_USERNAME` — иначе кнопка не показывается, как в `start.built_keyboard`); рядом
«Написать нам», только если задан `SUPPORT_URL` (D13). Строка `privacy` — только при
заданном `PRIVACY_URL` (D13).
"""

import logging
from datetime import date

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.handlers.common import ensure_profile
from app.bot.handlers.start import CHECK
from app.bot.router import router
from app.calendar import loader
from app.calendar.types import ReferenceFileError
from app.core.config import get_settings
from app.core.models import Profile
from app.core.texts import t

log = logging.getLogger(__name__)

ABOUT = "about:open"


def _last_checked(version: str) -> str | None:
    """`version` каталога (строка "YYYY-MM-DD") → «20.09.2026». Не парсится — None."""
    try:
        parsed = date.fromisoformat(version)
    except ValueError:
        return None
    return f"{parsed.day:02d}.{parsed.month:02d}.{parsed.year}"


def about_body() -> str:
    """Текст экрана: с датой сверки справочника или без неё, если справочник недоступен."""
    try:
        version = loader.get_reference().catalog.version
    except ReferenceFileError as exc:
        log.warning("справочник недоступен (about): %s", exc)
        return t("about.body_no_reference")
    last_checked = _last_checked(version)
    if last_checked is None:
        log.warning("about: version справочника не в формате YYYY-MM-DD: %r", version)
        return t("about.body_no_reference")
    return t("about.body", last_checked=last_checked)


def about_keyboard(profile: Profile) -> dict | None:
    """Один ряд: check-или-open (по `calendar_built_at`) + «Написать нам» (по `SUPPORT_URL`)."""
    settings = get_settings()
    row: list[dict] = []
    if profile.calendar_built_at is None:
        row.append(kb.callback(t("about.btn_check"), CHECK))
    elif settings.max_bot_username:
        row.append(kb.open_app(t("about.btn_open")))
    if settings.support_url:
        row.append(kb.link(t("about.btn_write"), settings.support_url))
    return kb.inline_keyboard(row) if row else None


async def show_about(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    await ctx.track("about_opened", {})
    profile = await ensure_profile(ctx)

    text = about_body()
    settings = get_settings()
    if settings.privacy_url:
        text = f"{text}\n{t('about.privacy', privacy_url=settings.privacy_url)}"

    keyboard = about_keyboard(profile)
    await ctx.reply(text, attachments=[keyboard] if keyboard else None)


@router.on_text("/about")
async def on_about_command(ctx: Ctx) -> None:
    await show_about(ctx)


@router.on_callback(ABOUT)
async def on_about_open(ctx: Ctx) -> None:
    await show_about(ctx)
