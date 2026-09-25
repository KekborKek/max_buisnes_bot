"""Экран 5 «Календарь готов» (docs/screens/05-calendar-ready.md, D16, D22, D27).

Payload кнопок: calendar:build — «Собрать календарь» (экран 3 и «Почему так?»). Он же —
«Повторить» после ошибки: сборка идемпотентна, повторное нажатие (и кнопка из старого
сообщения) присылает экран 5 снова, без дублей событий и уведомлений.

Здесь же общее для экранов 3 и 5: «сегодня» пользователя, дата «28 октября», ответ об ошибке
справочника. Даты — общим форматтером `bot/formatting.format_date` (DEBT-1, #67).
"""

import asyncio
import logging
from datetime import date
from zoneinfo import ZoneInfo

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.formatting import format_date
from app.bot.handlers import common
from app.bot.handlers.common import ensure_profile
from app.bot.router import router
from app.calendar import loader
from app.calendar.build import BuildResult, build_calendar
from app.calendar.types import MissingYearError, Reference, ReferenceFileError
from app.core.config import get_settings
from app.core.models import Profile
from app.core.texts import t

log = logging.getLogger(__name__)

BUILD = "calendar:build"
# Параметр запуска мини-аппа для кнопки «Настройки»: по нему открывается экран 13 (T14-13b).
SETTINGS_START_PARAM = "settings"

# Индикатор «печатает» — украшение: ждать ответа MAX дольше этого нельзя, сборка важнее.
_TYPING_TIMEOUT_S = 2


# --- общее для экранов 3 и 5 -------------------------------------------------------------


def today_for(profile: Profile) -> date:
    """«Сегодня» в часовом поясе пользователя."""
    return common.now().astimezone(ZoneInfo(profile.timezone)).date()


def lower_first(title: str) -> str:
    """Название из справочника внутри фразы: «Авансовый платёж…» → «авансовый платёж…».

    Аббревиатуру в начале («НДС: декларация») не трогаем.
    """
    if len(title) > 1 and title[1].isupper():
        return title
    return title[:1].lower() + title[1:]


def missing_step(profile: Profile) -> int | None:
    """Первый неотвеченный вопрос онбординга (кнопка из старого сообщения после сброса)."""
    if profile.income_band is None:
        return 1
    if profile.regime is None:
        return 2
    if profile.has_employees is None:
        return 3
    return None


async def ask_missing(ctx: Ctx, step: int, profile: Profile) -> None:
    # Импорт здесь: onboarding → nds_answer → calendar_ready, на уровне модуля — цикл.
    from app.bot.handlers import onboarding

    await onboarding.ask(ctx, step, profile)


def error_kind(exc: Exception) -> str:
    if isinstance(exc, MissingYearError):
        return "missing_year"
    return "reference_missing"


async def reply_error(
    ctx: Ctx, exc: Exception, *, where: str, kind: str, retry_payload: str
) -> None:
    """Ошибка данных справочника: common.error + «Повторить», причина — только в лог."""
    log.error("не удалось показать экран (%s, %s): %s", where, kind, exc)
    await ctx.track("error", {"where": where, "kind": kind})
    await ctx.reply(
        t("common.error"),
        attachments=[kb.inline_keyboard([kb.callback(t("common.btn_retry"), retry_payload)])],
    )


async def send_typing(ctx: Ctx) -> None:
    """Индикатор «печатает» (правило «Грузится»). Звать до первой записи в БД.

    `POST /chats/{chatId}/actions` для диалога не сверен **[сверить]** — ошибка не мешает.
    """
    if ctx.chat_id is None:
        return
    try:
        async with asyncio.timeout(_TYPING_TIMEOUT_S):
            await ctx.max.send_typing(ctx.chat_id)
    except Exception:
        log.warning("индикатор «печатает» не отправлен", exc_info=True)


# --- экран 5 -----------------------------------------------------------------------------


def count_word(count: int) -> str:
    """«событие / события / событий» по числу."""
    last_two, last = count % 100, count % 10
    if last == 1 and last_two != 11:
        return t("calendar_ready.count_one")
    if 2 <= last <= 4 and not 12 <= last_two <= 14:
        return t("calendar_ready.count_few")
    return t("calendar_ready.count_many")


def nearest_title(result: BuildResult, reference: Reference) -> str | None:
    """Название ближайшего события; None — показываем `calendar_ready.empty`."""
    if result.this_year == 0 or result.nearest_due_date is None:
        return None
    titles = {ob.id: ob.title for ob in reference.catalog.obligations}
    return titles.get(result.nearest_obligation_id or "")


def ready_text(profile: Profile, result: BuildResult, reference: Reference, today: date) -> str:
    title = nearest_title(result, reference)
    if title is None or result.nearest_due_date is None:
        text = t("calendar_ready.empty")
    else:
        text = t(
            "calendar_ready.body",
            count=result.this_year,
            count_word=count_word(result.this_year),
            next_date=format_date(result.nearest_due_date, today),
            next_title=lower_first(title),
        )
    if profile.has_employees:
        text += "\n\n" + t("calendar_ready.no_employee_items")  # D16
    return text


def ready_attachments(*, with_settings: bool = True) -> list[dict] | None:
    """«Открыть календарь» и «Настройки» в один ряд (open_app, D27).

    «Настройки» — экран 13 в мини-аппе: параметр запуска `settings` (как `item_…` у карточки).
    На пустом календаре её нет (экран 5, «Состояния»). Без MAX_BOT_USERNAME кнопок
    мини-аппа не показываем (keyboards.open_app).
    """
    if not get_settings().max_bot_username:
        return None
    row = [kb.open_app(t("calendar_ready.btn_open"))]
    if with_settings:
        row.append(kb.open_app(t("calendar_ready.btn_settings"), SETTINGS_START_PARAM))
    return [kb.inline_keyboard(row)]


@router.on_callback(BUILD)
async def on_build(ctx: Ctx) -> None:
    """«Собрать календарь»: сборка в транзакции обработчика (коммит — диспетчер) → экран 5."""
    if ctx.user_id is None:
        return
    await send_typing(ctx)  # сеть — до первой записи в БД
    profile = await ensure_profile(ctx)
    step = missing_step(profile)
    if step is not None:
        await ask_missing(ctx, step, profile)
        return

    moment = common.now()
    rebuild = profile.calendar_built_at is not None
    try:
        reference = loader.get_reference()
        # MissingYearError сборка бросает до первой записи — полусборки не бывает.
        result = await build_calendar(ctx.session, ctx.user_id, now=moment, reference=reference)
    except (ReferenceFileError, MissingYearError) as exc:
        await reply_error(
            ctx, exc, where="calendar_build", kind=error_kind(exc), retry_payload=BUILD
        )
        return

    seconds = int((moment - common.as_utc(profile.started_at)).total_seconds())
    await ctx.track(
        "calendar_built",
        {"items_count": result.this_year, "seconds_since_start": seconds, "rebuild": rebuild},
    )
    today = moment.astimezone(ZoneInfo(profile.timezone)).date()
    empty = nearest_title(result, reference) is None
    await ctx.reply(
        ready_text(profile, result, reference, today),
        attachments=ready_attachments(with_settings=not empty),
    )
