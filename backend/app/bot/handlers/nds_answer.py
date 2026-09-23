"""Экран 3 «Ответ про НДС» (docs/screens/03-nds-answer.md, D14, D18, D25).

Точка входа — `show_nds_answer`, её зовёт онбординг после четвёртого ответа.

Вариант текста (D14), первое совпадение сверху:
    regime ∈ {patent, ausn}        → nds.special_regime (приоритет над доходом)
    income_band ∈ {lt10, 10_20}    → nds.not_payer
    income_band ∈ {20_60, gt60}    → nds.payer (D18: больше 60 — как 20–60)
    income_band = unknown          → nds.unknown
`nds_payer = false` → в конце абзаца disclaimer.profile; режим был «Не знаю» → вторым
абзацем nds.regime_guessed.

Payload кнопок: calendar:build — «Собрать календарь» (экран 5, calendar_ready),
nds:why — «Почему так?», nds:show — «Повторить» после ошибки: экран 3 заново по профилю.
"""

from datetime import date

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.handlers import common
from app.bot.handlers.calendar_ready import (
    BUILD,
    ask_missing,
    error_kind,
    format_day,
    lower_first,
    missing_step,
    reply_error,
    today_for,
)
from app.bot.handlers.common import ensure_profile
from app.bot.router import router
from app.calendar import loader
from app.calendar.build import ProfileFacts, applies
from app.calendar.dates import obligation_dates
from app.calendar.types import (
    CalendarError,
    DueDate,
    MissingYearError,
    NdsConfig,
    Obligation,
    Reference,
    ReferenceFileError,
)
from app.core.models import Profile
from app.core.texts import t

WHY = "nds:why"
SHOW = "nds:show"

SPECIAL_REGIMES = ("patent", "ausn")
NOT_PAYER_BANDS = ("lt10", "10_20")
PAYER_BANDS = ("20_60", "gt60")

NO_NDS_REASON = "в справочнике нет записей с applies_if.nds_payer: true"


class NoNdsObligationsError(CalendarError):
    """Плательщик НДС, а НДС-записей в справочнике нет — ошибка данных аналитика."""


def variant(profile: Profile) -> str:
    """Ключ варианта текста в разделе nds (D14)."""
    if profile.regime in SPECIAL_REGIMES:
        return "special_regime"
    if profile.income_band in NOT_PAYER_BANDS:
        return "not_payer"
    if profile.income_band in PAYER_BANDS:
        return "payer"
    return "unknown"


def years_text(years: tuple[int, ...]) -> str:
    """(2025, 2026, 2027, 2028) → «2025–2028»; один год — «2029»."""
    ordered = sorted(years)
    if len(ordered) == 1:
        return str(ordered[0])
    return t("nds.years_range", first=ordered[0], last=ordered[-1])


def threshold_years(nds: NdsConfig, income_year: int) -> tuple[int, ...]:
    """Годы дохода того порога, который действует для `income_year`."""
    for threshold in nds.thresholds:
        if income_year in threshold.income_years:
            return threshold.income_years
    raise ReferenceFileError(f"в nds.yaml нет порога для дохода за {income_year} год")


def thresholds_text(nds: NdsConfig) -> str:
    """«20 млн ₽ — доходы за 2025–2028 годы, 15 млн ₽ — за 2029-й, 10 млн ₽ — за 2030-й»."""
    parts = []
    ordered = sorted(nds.thresholds, key=lambda th: min(th.income_years))
    for index, threshold in enumerate(ordered):
        limit = t("start.nds_limit", value=common.format_mln(threshold.limit_rub))
        years = threshold.income_years
        if index == 0:
            parts.append(t("nds.threshold_first", limit=limit, years=years_text(years)))
        elif len(years) == 1:
            parts.append(t("nds.threshold_single", limit=limit, year=years[0]))
        else:
            parts.append(t("nds.threshold_range", limit=limit, years=years_text(years)))
    return ", ".join(parts)


def nearest_nds_due(
    profile: Profile, reference: Reference, today: date
) -> tuple[Obligation, DueDate]:
    """Ближайшее НДС-обязательство профиля (applies_if.nds_payer: true), due_date ≥ сегодня.

    Остальные условия applies_if (режим и т. п.) сверяются с профилем. Следующий год смотрим,
    только если в текущем сроков не осталось: года может не быть в производственном
    календаре (MissingYearError).
    """
    facts = ProfileFacts(
        regime=profile.regime,
        income_band=profile.income_band,
        has_employees=profile.has_employees,
        nds_payer=True,
        timezone=profile.timezone,
    )
    candidates = [
        ob
        for ob in reference.catalog.obligations
        if ob.applies_if.nds_payer is True and applies(ob, facts)
    ]
    if not candidates:
        raise NoNdsObligationsError(NO_NDS_REASON)
    for year in (today.year, today.year + 1):
        best: tuple[Obligation, DueDate] | None = None
        for ob in candidates:
            for due in obligation_dates(ob, year, reference.workdays):
                if due.due_date >= today and (best is None or due.due_date < best[1].due_date):
                    best = (ob, due)
        if best is not None:
            return best
    raise NoNdsObligationsError(f"{NO_NDS_REASON} со сроком не раньше {today.isoformat()}")


def answer_text(
    profile: Profile, *, regime_guessed: bool, reference: Reference, today: date
) -> str:
    """Текст экрана 3. Ошибки данных — CalendarError (в т. ч. NoNdsObligationsError)."""
    kind = variant(profile)
    if kind == "special_regime":
        text = t("nds.special_regime", regime_name=t(f"onboarding.q2_{profile.regime}"))
    elif kind == "not_payer":
        years = threshold_years(reference.nds, common.income_year(profile))
        text = t("nds.not_payer", limit=common.nds_limit_text(profile), years=years_text(years))
    elif kind == "payer":
        limit = common.nds_limit_text(profile)
        ob, due = nearest_nds_due(profile, reference, today)
        shift_note = ""
        if due.original_date != due.due_date:
            shift_note = t("nds.shift_note", original_date=format_day(due.original_date, today))
        text = t(
            "nds.payer",
            limit=limit,
            next_title=lower_first(ob.title),
            next_date=format_day(due.due_date, today),
            shift_note=shift_note,
        )
    else:
        text = t("nds.unknown", limit=common.nds_limit_text(profile))
    if profile.nds_payer is False:
        text += " " + t("disclaimer.profile")
    if regime_guessed:
        text += "\n\n" + t("nds.regime_guessed")
    return text


def answer_keyboard() -> dict:
    """Один ряд: «Собрать календарь», «Почему так?»."""
    return kb.inline_keyboard(
        [kb.callback(t("nds.btn_build"), BUILD), kb.callback(t("nds.btn_why"), WHY)]
    )


def why_keyboard(nds: NdsConfig) -> dict:
    """Ссылки «Закон» и «Разъяснения ФНС» из nds.yaml, ниже — снова «Собрать календарь»."""
    return kb.inline_keyboard(
        [kb.link(t("nds.btn_law"), nds.law_url), kb.link(t("nds.btn_fns"), nds.fns_guide_url)],
        [kb.callback(t("nds.btn_build"), BUILD)],
    )


async def show_nds_answer(ctx: Ctx, profile: Profile, *, regime_guessed: bool) -> None:
    """Экран 3. `profile` — уже с `nds_payer`; `regime_guessed` — режим был «Не знаю»."""
    try:
        text = answer_text(
            profile,
            regime_guessed=regime_guessed,
            reference=loader.get_reference(),
            today=today_for(profile),
        )
    except NoNdsObligationsError as exc:
        await reply_error(
            ctx, exc, where="nds_answer", kind="no_nds_obligations", retry_payload=SHOW
        )
        return
    except (ReferenceFileError, MissingYearError) as exc:
        await reply_error(ctx, exc, where="nds_answer", kind=error_kind(exc), retry_payload=SHOW)
        return
    await ctx.track(
        "nds_result_shown",
        {
            "income_band": profile.income_band,
            "regime": profile.regime,
            "nds_payer": profile.nds_payer,
        },
    )
    await ctx.reply(text, attachments=[answer_keyboard()])


@router.on_callback(SHOW)
async def on_show(ctx: Ctx) -> None:
    """«Повторить» после ошибки экрана 3: ответы профиля сохранены, показываем заново."""
    if ctx.user_id is None:
        return
    profile = await ensure_profile(ctx)
    step = missing_step(profile)
    if step is not None:
        await ask_missing(ctx, step, profile)
        return
    _, data = await ctx.get_state()
    await show_nds_answer(ctx, profile, regime_guessed=bool(data.get("regime_guessed")))


@router.on_callback(WHY)
async def on_why(ctx: Ctx) -> None:
    """«Почему так?»: пороги и закон из nds.yaml, ссылки на закон и ФНС, снова «Собрать»."""
    if ctx.user_id is None:
        return
    try:
        nds = loader.get_reference().nds
    except ReferenceFileError as exc:
        await reply_error(ctx, exc, where="nds_why", kind=error_kind(exc), retry_payload=WHY)
        return
    await ctx.track("nds_why_opened")
    await ctx.reply(
        t("nds.why", thresholds=thresholds_text(nds), law=nds.law),
        attachments=[why_keyboard(nds)],
    )
