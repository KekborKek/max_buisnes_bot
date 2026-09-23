"""Общее для экранов 1–2: профиль, «прошлый год», порог НДС из справочника, ответ об ошибке."""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.calendar import dates, loader
from app.calendar.types import ReferenceFileError
from app.core.models import DEFAULT_TIMEZONE, Profile, User
from app.core.texts import t

log = logging.getLogger(__name__)

# Перевод рублей в миллионы для подписи «20 млн ₽» — единица измерения, не налоговые данные.
_RUB_IN_MLN = 1_000_000


def now() -> datetime:
    """Текущий момент (UTC). Отдельной функцией, чтобы тесты не зависели от даты прогона."""
    return datetime.now(UTC)


def as_utc(moment: datetime) -> datetime:
    """SQLite возвращает DateTime без пояса — считаем, что там UTC (так мы его и пишем)."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def income_year(profile: Profile | None) -> int:
    """Год дохода для НДС: текущий год − 1 в часовом поясе пользователя (D2)."""
    tz_name = (profile.timezone if profile is not None else None) or DEFAULT_TIMEZONE
    return now().astimezone(ZoneInfo(tz_name)).year - 1


def format_mln(limit_rub: int) -> str:
    """Рубли → миллионы: целые без дроби («15»), нецелые — с запятой (2 400 000 → «2,4»)."""
    if limit_rub % _RUB_IN_MLN == 0:
        return str(limit_rub // _RUB_IN_MLN)
    value = f"{limit_rub / _RUB_IN_MLN:.2f}".rstrip("0").rstrip(".")
    return value.replace(".", ",")


def nds_limit_text(profile: Profile | None) -> str:
    """Порог НДС для дохода за прошлый год из nds.yaml: «20 млн ₽».

    Справочника нет или на этот год порога нет — ReferenceFileError: показывать
    приветствие без порога нельзя, число в коде не держим.
    """
    year = income_year(profile)
    limit = dates.nds_limit_for(year, loader.get_reference().nds)
    if limit is None:
        raise ReferenceFileError(f"в nds.yaml нет порога для дохода за {year} год")
    return t("start.nds_limit", value=format_mln(limit))


async def ensure_profile(ctx: Ctx) -> Profile:
    """Профиль пользователя; нет — создаёт `User` и `Profile` (started_at = сейчас).

    Relationship у моделей нет, поэтому `User` сбрасываем в базу до вставки `Profile`,
    иначе внешний ключ не найдёт строку. Повторный вызов дублей не создаёт.
    """
    assert ctx.user_id is not None
    profile = await ctx.session.get(Profile, ctx.user_id)
    if profile is not None:
        return profile
    if await ctx.session.get(User, ctx.user_id) is None:
        ctx.session.add(User(user_id=ctx.user_id, name=ctx.user_name))
        await ctx.session.flush()
    profile = Profile(user_id=ctx.user_id, timezone=DEFAULT_TIMEZONE, started_at=now())
    ctx.session.add(profile)
    await ctx.session.flush()
    return profile


async def reply_reference_error(
    ctx: Ctx, exc: ReferenceFileError, *, where: str, retry_payload: str
) -> None:
    """Справочник недоступен: общая ошибка + «Повторить», причина — только в лог."""
    log.error("справочник недоступен (%s): %s", where, exc)
    await ctx.track("error", {"where": where, "kind": "reference_missing"})
    await ctx.reply(
        t("common.error"),
        attachments=[kb.inline_keyboard([kb.callback(t("common.btn_retry"), retry_payload)])],
    )
