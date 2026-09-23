"""T5a (#46): экраны 1 и 2 бота — первое сообщение и четыре вопроса.

Справочники — фикстуры backend/tests/fixtures (порог 20 млн для доходов 2025–2026,
15 млн для 2027). «Сейчас» заморожено, чтобы «прошлый год» не зависел от даты прогона.
"""

import itertools
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.bot.dispatcher import process_update
from app.bot.handlers import common, onboarding, start
from app.calendar import loader
from app.calendar.types import ReferenceFileError
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import DialogState, Event, Profile, User
from app.core.texts import t
from tests.conftest import load_update

pytestmark = pytest.mark.usefixtures("fixture_reference")

USER_ID = 42
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
NBSP = " "
BOT_DIR = Path(__file__).resolve().parents[1] / "app" / "bot"

_seq = itertools.count(1)


# --- помощники ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Подменяемое «сейчас»: clock["now"] можно сдвигать внутри теста."""
    state = {"now": NOW}
    monkeypatch.setattr(common, "now", lambda: state["now"])
    return state


def press(payload: str) -> dict:
    """Нажатие inline-кнопки с данным payload (у каждого — свой callback_id)."""
    n = next(_seq)
    update = load_update("callback_start_check")
    update["timestamp"] += n
    update["callback"] = dict(update["callback"], payload=payload, callback_id=f"cb-onb-{n}")
    return update


def write(text: str) -> dict:
    """Текстовое сообщение пользователя."""
    n = next(_seq)
    update = load_update("message_created")
    update["timestamp"] += n
    update["message"] = dict(update["message"], body={"mid": f"mid-onb-{n}", "text": text})
    return update


def started(payload: str | None = "point_7") -> dict:
    """bot_started (кнопка «Начать» или диплинк ?start=)."""
    update = load_update("bot_started")
    update["timestamp"] += next(_seq)
    update["payload"] = payload
    return update


def keyboard(message: dict) -> list[list[dict]]:
    return message["attachments"][0]["payload"]["buttons"]


def payloads(message: dict) -> list[list[str | None]]:
    return [[b.get("payload") for b in row] for row in keyboard(message)]


def labels(message: dict) -> list[list[str]]:
    return [[b["text"] for b in row] for row in keyboard(message)]


async def run(fake_max, *updates: dict) -> dict:
    for update in updates:
        await process_update(update, fake_max)
    return fake_max.sent[-1]


async def get_profile() -> Profile | None:
    async with SessionLocal() as s:
        return await s.get(Profile, USER_ID)


async def get_state() -> DialogState | None:
    async with SessionLocal() as s:
        return await s.get(DialogState, USER_ID)


async def events(name: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Event.props).where(Event.name == name).order_by(Event.id))
        return list(rows)


async def go_to_question(fake_max, step: int) -> dict:
    """/start → «Проверить НДС» → ответы до вопроса `step`."""
    answers = ["onb:1:20_60", "onb:2:usn6", "onb:3:no"][: step - 1]
    return await run(fake_max, started(), press("start:check"), *map(press, answers))


def greeting_for(limit_text: str) -> str:
    return t("start.greeting", nds_limit=limit_text)


# --- экран 1 -----------------------------------------------------------------------------


async def test_bot_started_greets_with_limit_from_reference(fake_max):
    msg = await run(fake_max, started("partner_1"))

    assert msg["text"] == greeting_for(f"20 млн{NBSP}₽")
    assert labels(msg) == [[t("start.btn_check"), t("start.btn_about")]]
    assert payloads(msg) == [["start:check", "about:open"]]
    assert await events("bot_started") == [{"start_param": "partner_1"}]


async def test_first_start_creates_user_and_profile(fake_max):
    await run(fake_max, started())

    profile = await get_profile()
    assert profile is not None
    assert profile.timezone == "Europe/Moscow"
    assert profile.income_band is None and profile.regime is None
    assert common.as_utc(profile.started_at) == NOW


async def test_start_command_text_greets_without_start_param(fake_max):
    msg = await run(fake_max, load_update("message_created_start"))

    assert msg["text"] == greeting_for(f"20 млн{NBSP}₽")
    assert await events("bot_started") == [{"start_param": None}]


async def test_repeat_start_does_not_duplicate_profile(fake_max, clock):
    await run(fake_max, started())
    clock["now"] = NOW + timedelta(hours=1)
    await run(fake_max, started(None), write("/start"))

    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 1
        assert await s.scalar(select(func.count()).select_from(Profile)) == 1
    profile = await get_profile()
    assert common.as_utc(profile.started_at) == NOW  # started_at — только первый /start
    assert len(await events("bot_started")) == 3


async def test_limit_and_income_year_follow_current_year(fake_max, clock):
    """В 2028 году доход — за 2027-й, порог из nds.yaml для 2027 — 15 млн."""
    clock["now"] = datetime(2028, 3, 1, 9, 0, tzinfo=UTC)

    msg = await run(fake_max, started())
    assert msg["text"] == greeting_for(f"15 млн{NBSP}₽")

    msg = await run(fake_max, press("start:check"))
    assert msg["text"] == "Вопрос 1 из 4. Какой доход был за 2027 год?"


async def test_income_year_is_taken_in_user_timezone(fake_max, clock):
    """31 декабря 22:00 UTC — в Москве уже 1 января: «прошлый год» — уходящий."""
    clock["now"] = datetime(2026, 12, 31, 22, 0, tzinfo=UTC)

    msg = await run(fake_max, started(), press("start:check"))

    assert msg["text"] == "Вопрос 1 из 4. Какой доход был за 2026 год?"


async def test_q1_text_today_is_exactly_as_in_d2(fake_max):
    msg = await go_to_question(fake_max, 1)

    assert msg["text"] == "Вопрос 1 из 4. Какой доход был за 2025 год?"


@pytest.mark.parametrize(
    ("limit_rub", "expected"),
    [(20_000_000, "20"), (15_000_000, "15"), (2_400_000, "2,4"), (1_250_000, "1,25")],
)
def test_format_mln(limit_rub, expected):
    assert common.format_mln(limit_rub) == expected


def test_no_nds_threshold_number_in_bot_code_and_texts():
    """Порог — только из nds.yaml: ни в коде бота, ни в приветствии числа нет."""
    pattern = re.compile(r"20[ _ ]?000[ _ ]?000|\b2e7\b")
    for path in BOT_DIR.rglob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path
    assert "{nds_limit}" in t("start.greeting")
    assert not re.search(r"\d+\s*млн", t("start.greeting"))


async def test_repeat_start_mid_onboarding_greets_and_keeps_answers(fake_max):
    await go_to_question(fake_max, 3)

    msg = await run(fake_max, write("/start"))

    assert msg["text"] == greeting_for(f"20 млн{NBSP}₽")
    profile = await get_profile()
    assert (profile.income_band, profile.regime) == ("20_60", "usn6")
    assert (await get_state()).state is None

    # следующее нажатие «Проверить НДС» сбрасывает незаконченный онбординг
    msg = await run(fake_max, press("start:check"))
    assert msg["text"].startswith("Вопрос 1 из 4.")
    profile = await get_profile()
    assert (profile.income_band, profile.regime, profile.has_employees) == (None, None, None)


async def _mark_built() -> datetime:
    built_at = NOW - timedelta(days=1)
    async with SessionLocal() as s:
        profile = await s.get(Profile, USER_ID)
        profile.calendar_built_at = built_at
        profile.income_band, profile.regime, profile.has_employees = "lt10", "usn6", False
        await s.commit()
    return built_at


async def test_repeat_start_after_build_shows_already_built(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "pareto_calendar_bot")
    await run(fake_max, started())
    built_at = await _mark_built()

    msg = await run(fake_max, started())

    assert msg["text"] == t("start.already_built")
    [[open_btn, rebuild_btn]] = keyboard(msg)
    assert open_btn == {
        "type": "open_app",
        "text": t("start.btn_open"),
        "web_app": "pareto_calendar_bot",
    }
    assert rebuild_btn["payload"] == "start:rebuild"
    profile = await get_profile()
    assert common.as_utc(profile.calendar_built_at) == built_at


async def test_already_built_without_bot_username_hides_open_app(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")
    await run(fake_max, started())
    await _mark_built()

    msg = await run(fake_max, started())

    assert payloads(msg) == [["start:rebuild"]]


async def test_rebuild_starts_onboarding_and_keeps_calendar(fake_max):
    await run(fake_max, started())
    built_at = await _mark_built()

    msg = await run(fake_max, press("start:rebuild"))

    assert msg["text"].startswith("Вопрос 1 из 4.")
    profile = await get_profile()
    assert common.as_utc(profile.calendar_built_at) == built_at
    assert profile.income_band == "lt10"  # ответы остаются до перезаписи новыми


async def test_about_button_until_t9_behaves_as_unknown_action(fake_max):
    msg = await run(fake_max, started(), press("about:open"))

    assert msg["text"] == t("errors.unknown")
    assert payloads(msg) == [["start:check", "about:open"]]


async def test_missing_reference_on_start_shows_error_and_retry(fake_max, monkeypatch, caplog):
    real = loader.get_reference

    def broken():
        raise ReferenceFileError("нет файла nds.yaml")

    monkeypatch.setattr(loader, "get_reference", broken)
    msg = await run(fake_max, started())

    assert msg["text"] == t("common.error")
    assert labels(msg) == [[t("common.btn_retry")]]
    assert payloads(msg) == [["start:retry"]]
    assert await events("error") == [{"where": "start", "kind": "reference_missing"}]
    assert "нет файла nds.yaml" in caplog.text

    monkeypatch.setattr(loader, "get_reference", real)
    msg = await run(fake_max, press("start:retry"))

    assert msg["text"] == greeting_for(f"20 млн{NBSP}₽")
    assert len(await events("bot_started")) == 1  # «Повторить» — не новый /start


# --- экран 2 -----------------------------------------------------------------------------


async def test_full_path_by_buttons(fake_max, clock):
    msg = await run(fake_max, started(), press("start:check"))
    assert payloads(msg) == [
        ["onb:1:lt10", "onb:1:10_20"],
        ["onb:1:20_60", "onb:1:gt60"],
        ["onb:1:unknown"],
    ]

    msg = await run(fake_max, press("onb:1:20_60"))
    assert msg["text"] == t("onboarding.q2")
    assert payloads(msg) == [
        ["onb:2:usn6", "onb:2:usn15"],
        ["onb:2:patent", "onb:2:ausn"],
        ["onb:2:unknown", "onb:2:back"],
    ]

    msg = await run(fake_max, press("onb:2:usn6"))
    assert msg["text"] == t("onboarding.q3")
    assert payloads(msg) == [["onb:3:no", "onb:3:yes"], ["onb:3:back"]]

    msg = await run(fake_max, press("onb:3:no"))
    assert msg["text"] == t("onboarding.q4")
    assert payloads(msg) == [["onb:4:Europe/Moscow", "onb:4:other"], ["onb:4:back"]]

    clock["now"] = NOW + timedelta(seconds=95)
    sent_before = len(fake_max.sent)
    msg = await run(fake_max, press("onb:4:Europe/Moscow"))

    # экран 3 приходит сам, без дополнительного нажатия. Профиль 20–60 — плательщик, а НДС-записей
    # в фикстурах нет: экран 3 отвечает ошибкой данных с «Повторить» (варианты — test_nds_answer)
    assert len(fake_max.sent) == sent_before + 1
    assert payloads(msg) == [["nds:show"]]

    profile = await get_profile()
    assert profile.income_band == "20_60"
    assert profile.regime == "usn6"
    assert profile.has_employees is False
    assert profile.timezone == "Europe/Moscow"
    assert profile.nds_payer is True  # 20–60 млн при пороге 20 млн из фикстуры
    assert (await get_state()).state is None

    assert await events("onboarding_answer") == [
        {"step": 1, "value": "20_60"},
        {"step": 2, "value": "usn6"},
        {"step": 3, "value": "no"},
        {"step": 4, "value": "Europe/Moscow"},
    ]
    assert await events("onboarding_completed") == [{"seconds_since_start": 95}]


async def test_completion_calls_screen_3_entry_point(fake_max, monkeypatch):
    calls = []

    async def fake_screen_3(ctx, profile, *, regime_guessed):
        calls.append((profile.user_id, profile.nds_payer, regime_guessed))

    monkeypatch.setattr(onboarding, "show_nds_answer", fake_screen_3)
    await go_to_question(fake_max, 4)
    await run(fake_max, press("onb:4:Europe/Moscow"))

    assert calls == [(USER_ID, True, False)]


async def test_back_returns_to_previous_question_and_answer_is_overwritten(fake_max):
    await go_to_question(fake_max, 2)

    msg = await run(fake_max, press("onb:2:back"))
    assert msg["text"].startswith("Вопрос 1 из 4.")
    assert (await get_state()).state == "onb:q1"

    msg = await run(fake_max, press("onb:1:lt10"))
    assert msg["text"] == t("onboarding.q2")
    assert (await get_profile()).income_band == "lt10"


@pytest.mark.parametrize(
    ("step", "back", "expected_state", "expected_text"),
    [
        (3, "onb:3:back", "onb:q2", "onboarding.q2"),
        (4, "onb:4:back", "onb:q3", "onboarding.q3"),
    ],
)
async def test_back_from_later_questions(fake_max, step, back, expected_state, expected_text):
    await go_to_question(fake_max, step)

    msg = await run(fake_max, press(back))

    assert msg["text"] == t(expected_text)
    assert (await get_state()).state == expected_state


async def test_unknown_income(fake_max):
    await go_to_question(fake_max, 1)
    await run(
        fake_max,
        press("onb:1:unknown"),
        press("onb:2:usn6"),
        press("onb:3:no"),
        press("onb:4:Europe/Moscow"),
    )

    profile = await get_profile()
    assert profile.income_band == "unknown"
    assert profile.nds_payer is None  # D25: определить нельзя


async def test_unknown_regime_sets_usn6_and_flags_guess(fake_max, monkeypatch):
    calls = []

    async def fake_screen_3(ctx, profile, *, regime_guessed):
        calls.append(regime_guessed)

    monkeypatch.setattr(onboarding, "show_nds_answer", fake_screen_3)
    await go_to_question(fake_max, 2)
    await run(fake_max, press("onb:2:unknown"), press("onb:3:no"), press("onb:4:Europe/Moscow"))

    assert (await get_profile()).regime == "usn6"
    assert (await events("onboarding_answer"))[1] == {"step": 2, "value": "unknown"}
    assert calls == [True]


async def test_other_timezone_list_and_iana_saved(fake_max):
    await go_to_question(fake_max, 4)

    msg = await run(fake_max, press("onb:4:other"))
    assert msg["text"] == t("onboarding.q4_list")
    assert payloads(msg) == [
        ["onb:4:Europe/Kaliningrad", "onb:4:Europe/Samara"],
        ["onb:4:Asia/Yekaterinburg", "onb:4:Asia/Omsk"],
        ["onb:4:Asia/Krasnoyarsk", "onb:4:Asia/Irkutsk"],
        ["onb:4:Asia/Yakutsk", "onb:4:Asia/Vladivostok"],
        ["onb:4:Asia/Magadan", "onb:4:Asia/Kamchatka"],
        ["onb:tz:back"],
    ]
    assert labels(msg)[0] == ["Калининград (UTC+2)", "Самара (UTC+4)"]

    msg = await run(fake_max, press("onb:4:Asia/Vladivostok"))

    assert payloads(msg) == [["nds:show"]]  # экран 3, см. test_full_path_by_buttons
    assert (await get_profile()).timezone == "Asia/Vladivostok"


async def test_back_from_timezone_list_returns_to_q4(fake_max):
    await go_to_question(fake_max, 4)

    msg = await run(fake_max, press("onb:4:other"), press("onb:tz:back"))

    assert msg["text"] == t("onboarding.q4")
    assert (await get_state()).state == "onb:q4"


async def test_text_instead_of_button_repeats_current_question_buttons(fake_max):
    q2 = await go_to_question(fake_max, 2)

    msg = await run(fake_max, write("у меня упрощёнка"))

    assert msg["text"] == t("onboarding.use_buttons")
    assert keyboard(msg) == keyboard(q2)
    assert (await get_state()).state == "onb:q2"

    # сценарий не сломан: кнопка после текста работает
    msg = await run(fake_max, press("onb:2:usn15"))
    assert msg["text"] == t("onboarding.q3")


async def test_text_in_timezone_list_repeats_timezone_buttons(fake_max):
    await go_to_question(fake_max, 4)
    tz_list = await run(fake_max, press("onb:4:other"))

    msg = await run(fake_max, write("Новосибирск"))

    assert msg["text"] == t("onboarding.use_buttons")
    assert keyboard(msg) == keyboard(tz_list)


async def test_button_from_old_message_is_accepted(fake_max):
    """На вопросе 3 нажали кнопку вопроса 1: ответ принят, дальше — вопрос 2."""
    await go_to_question(fake_max, 3)

    msg = await run(fake_max, press("onb:1:gt60"))

    assert msg["text"] == t("onboarding.q2")
    assert (await get_profile()).income_band == "gt60"
    assert (await get_state()).state == "onb:q2"


async def test_old_q4_button_after_reset_asks_missing_question(fake_max):
    """После «Проверить НДС» ответы стёрты — старая кнопка вопроса 4 не завершает онбординг."""
    await go_to_question(fake_max, 4)
    await run(fake_max, press("start:check"))

    msg = await run(fake_max, press("onb:4:Europe/Moscow"))

    assert msg["text"].startswith("Вопрос 1 из 4.")
    assert await events("onboarding_completed") == []


async def test_missing_reference_on_completion_keeps_answer_and_retries(fake_max, monkeypatch):
    await go_to_question(fake_max, 4)
    real = loader.get_reference

    def broken():
        raise ReferenceFileError("нет файла nds.yaml")

    monkeypatch.setattr(loader, "get_reference", broken)
    msg = await run(fake_max, press("onb:4:Europe/Moscow"))

    assert msg["text"] == t("common.error")
    assert payloads(msg) == [["onb:4:Europe/Moscow"]]
    assert (await get_profile()).timezone == "Europe/Moscow"
    assert await events("error") == [{"where": "onboarding", "kind": "reference_missing"}]

    monkeypatch.setattr(loader, "get_reference", real)
    msg = await run(fake_max, press("onb:4:Europe/Moscow"))

    assert payloads(msg) == [["nds:show"]]  # экран 3, см. test_full_path_by_buttons
    assert len(await events("onboarding_completed")) == 1


async def test_duplicate_answer_delivery_counts_once(fake_max):
    await go_to_question(fake_max, 1)
    update = press("onb:1:lt10")

    await run(fake_max, update, update)

    assert await events("onboarding_answer") == [{"step": 1, "value": "lt10"}]


# --- форма кнопок ---------------------------------------------------------------------------


def _all_keyboards() -> dict[str, dict]:
    boards = {f"q{step}": onboarding.question_keyboard(step) for step in (1, 2, 3, 4)}
    boards["tz"] = onboarding.timezone_keyboard()
    boards["start"] = start.start_keyboard()
    boards["built"] = start.built_keyboard()
    return boards


def test_button_labels_fit_20_chars_and_two_per_row(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "pareto_calendar_bot")
    for name, board in _all_keyboards().items():
        for row in board["payload"]["buttons"]:
            assert 1 <= len(row) <= 2, name
            for button in row:
                assert len(button["text"]) <= 20, (name, button["text"])
                assert not button["text"].startswith("["), (name, button["text"])  # ключ найден
    assert len(t("common.btn_retry")) <= 20
