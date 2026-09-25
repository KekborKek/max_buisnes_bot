"""Экран 2 «Четыре вопроса» (docs/screens/02-onboarding.md).

Состояние шага — в `DialogState`:
    state: onb:q1 | onb:q2 | onb:q3 | onb:q4 | onb:tz (открыт список поясов) | None
    data:  {"regime_guessed": bool} — режим был «Не знаю», нужно экрану 3
Ответы сразу пишутся в `Profile`.

Payload кнопок — `onb:<шаг>:<значение>`:
    onb:1:{lt10|10_20|20_60|gt60|unknown}   onb:2:{usn6|usn15|patent|ausn|unknown}
    onb:3:{no|yes}                          onb:4:<IANA>   onb:4:other — список поясов
    onb:<шаг>:back — к предыдущему вопросу  onb:tz:back — из списка поясов к вопросу 4
Шаг берётся из payload, а не из состояния: кнопка из старого сообщения принимается,
и бот продолжает со следующего за ней вопроса.
"""

import logging

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.handlers import common
from app.bot.handlers.common import ensure_profile, income_year, reply_reference_error
from app.bot.handlers.nds_answer import show_nds_answer
from app.bot.router import router
from app.calendar import dates, loader
from app.calendar.timezones import OTHER_TIMEZONES, TIMEZONES
from app.calendar.types import ReferenceFileError
from app.core.models import DEFAULT_TIMEZONE, Profile
from app.core.texts import t

log = logging.getLogger(__name__)

PREFIX = "onb:"
STATES = {step: f"onb:q{step}" for step in (1, 2, 3, 4)}
TZ_STATE = "onb:tz"
BACK = "back"
UNKNOWN = "unknown"
OTHER_TZ = "other"

INCOME_BANDS = ("lt10", "10_20", "20_60", "gt60")
REGIMES = ("usn6", "usn15", "patent", "ausn")
REGIME_IF_UNKNOWN = "usn6"  # «Не знаю» на режиме → самый частый (экран 2)
EMPLOYEES = {"no": False, "yes": True}

ANSWERS: dict[int, tuple[str, ...]] = {
    1: (*INCOME_BANDS, UNKNOWN),
    2: (*REGIMES, UNKNOWN),
    3: tuple(EMPLOYEES),
    4: TIMEZONES,
}


def _button(key: str, step: int | str, value: str) -> dict:
    return kb.callback(t(f"onboarding.{key}"), f"{PREFIX}{step}:{value}")


def question_keyboard(step: int) -> dict:
    """Раскладка из таблицы экрана 2: не больше двух кнопок в ряду."""
    if step == 1:
        return kb.inline_keyboard(
            [_button("q1_lt10", 1, "lt10"), _button("q1_10_20", 1, "10_20")],
            [_button("q1_20_60", 1, "20_60"), _button("q1_gt60", 1, "gt60")],
            [_button("btn_unknown", 1, UNKNOWN)],
        )
    if step == 2:
        return kb.inline_keyboard(
            [_button("q2_usn6", 2, "usn6"), _button("q2_usn15", 2, "usn15")],
            [_button("q2_patent", 2, "patent"), _button("q2_ausn", 2, "ausn")],
            [_button("btn_unknown", 2, UNKNOWN), _button("btn_back", 2, BACK)],
        )
    if step == 3:
        return kb.inline_keyboard(
            [_button("q3_no", 3, "no"), _button("q3_yes", 3, "yes")],
            [_button("btn_back", 3, BACK)],
        )
    if step == 4:
        return kb.inline_keyboard(
            [_button("q4_msk", 4, DEFAULT_TIMEZONE), _button("q4_other", 4, OTHER_TZ)],
            [_button("btn_back", 4, BACK)],
        )
    raise ValueError(f"нет вопроса {step}")


def timezone_keyboard() -> dict:
    """D3: пять рядов по два пояса + «Назад» (к вопросу 4)."""
    buttons = [kb.callback(t(f"onboarding.tz.{tz}"), f"{PREFIX}4:{tz}") for tz in OTHER_TIMEZONES]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return kb.inline_keyboard(*rows, [_button("btn_back", "tz", BACK)])


def question_text(step: int, profile: Profile | None) -> str:
    if step == 1:
        return t("onboarding.q1", income_year=income_year(profile))
    return t(f"onboarding.q{step}")


async def ask(ctx: Ctx, step: int, profile: Profile) -> None:
    """Задаёт вопрос `step` и запоминает шаг; накопленные данные состояния не трогает."""
    _, data = await ctx.get_state()
    await ctx.set_state(STATES[step], data)
    await ctx.reply(question_text(step, profile), attachments=[question_keyboard(step)])


async def ask_timezone(ctx: Ctx) -> None:
    _, data = await ctx.get_state()
    await ctx.set_state(TZ_STATE, data)
    await ctx.reply(t("onboarding.q4_list"), attachments=[timezone_keyboard()])


async def begin(ctx: Ctx) -> None:
    """«Проверить НДС» / «Собрать заново»: незаконченный онбординг сбрасывается, вопрос 1.

    Пока календарь не собран, прежние ответы стираются. После сборки они остаются до
    перезаписи новыми: по ним собран текущий календарь, отметки и свои задачи не трогаем.
    """
    if ctx.user_id is None:
        return
    profile = await ensure_profile(ctx)
    if profile.calendar_built_at is None:
        profile.income_band = None
        profile.regime = None
        profile.has_employees = None
        profile.nds_payer = None
    await ctx.set_state(STATES[1], {})
    await ctx.reply(question_text(1, profile), attachments=[question_keyboard(1)])


def _first_missing_step(profile: Profile) -> int | None:
    if profile.income_band is None:
        return 1
    if profile.regime is None:
        return 2
    if profile.has_employees is None:
        return 3
    return None


async def complete(ctx: Ctx, profile: Profile) -> None:
    """После четвёртого ответа: nds_payer → onboarding_completed → экран 3 (T5b)."""
    missing = _first_missing_step(profile)
    if missing is not None:
        # Например, нажали вопрос 4 из старого сообщения после «Проверить НДС»:
        # считать НДС не из чего — задаём первый неотвеченный вопрос.
        await ask(ctx, missing, profile)
        return
    try:
        nds = loader.get_reference().nds
    except ReferenceFileError as exc:
        # Ответ уже сохранён; «Повторить» повторяет ту же кнопку.
        await reply_reference_error(
            ctx, exc, where="onboarding", retry_payload=ctx.payload or f"{PREFIX}4:{BACK}"
        )
        return
    profile.nds_payer = dates.nds_payer(profile.income_band, income_year(profile), nds)
    _, data = await ctx.get_state()
    await ctx.set_state(None, data)
    seconds = int((common.now() - common.as_utc(profile.started_at)).total_seconds())
    await ctx.track("onboarding_completed", {"seconds_since_start": seconds})
    await show_nds_answer(ctx, profile, regime_guessed=bool(data.get("regime_guessed")))


def _parse(payload: str | None) -> tuple[str, str] | None:
    parts = (payload or "").split(":", 2)
    if len(parts) != 3 or parts[0] + ":" != PREFIX:
        return None
    return parts[1], parts[2]


async def _save_answer(ctx: Ctx, profile: Profile, step: int, value: str) -> None:
    if step == 1:
        profile.income_band = value
    elif step == 2:
        guessed = value == UNKNOWN
        profile.regime = REGIME_IF_UNKNOWN if guessed else value
        state, data = await ctx.get_state()
        await ctx.set_state(state, {**data, "regime_guessed": guessed})
    elif step == 3:
        profile.has_employees = EMPLOYEES[value]
    elif step == 4:
        profile.timezone = value
    await ctx.track("onboarding_answer", {"step": step, "value": value})


async def repeat_current(ctx: Ctx) -> None:
    """Текст вместо кнопки (или непонятная кнопка): `use_buttons` и кнопки текущего шага."""
    state, _ = await ctx.get_state()
    if state == TZ_STATE:
        keyboard = timezone_keyboard()
    else:
        step = next((s for s, name in STATES.items() if name == state), 1)
        keyboard = question_keyboard(step)
    await ctx.reply(t("onboarding.use_buttons"), attachments=[keyboard])


@router.on_callback(PREFIX)
async def on_onboarding_button(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    parsed = _parse(ctx.payload)
    if parsed is None:
        await repeat_current(ctx)
        return
    step_raw, value = parsed
    profile = await ensure_profile(ctx)

    if step_raw == "tz" and value == BACK:
        await ask(ctx, 4, profile)
        return
    if not step_raw.isdigit() or int(step_raw) not in ANSWERS:
        log.warning("неизвестный шаг онбординга в payload %r", ctx.payload)
        await repeat_current(ctx)
        return
    step = int(step_raw)

    if value == BACK:
        await ask(ctx, max(step - 1, 1), profile)
        return
    if step == 4 and value == OTHER_TZ:
        await ask_timezone(ctx)
        return
    if value not in ANSWERS[step]:
        log.warning("неизвестный ответ онбординга в payload %r", ctx.payload)
        await repeat_current(ctx)
        return

    await _save_answer(ctx, profile, step, value)
    if step < 4:
        await ask(ctx, step + 1, profile)
    else:
        await complete(ctx, profile)


async def on_text_during_onboarding(ctx: Ctx) -> None:
    """Текст вместо нажатия — в том числе «вернулся через час»: сценарий не ломается."""
    await repeat_current(ctx)


for _state in (*STATES.values(), TZ_STATE):
    router.on_state(_state)(on_text_during_onboarding)
