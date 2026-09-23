"""T5b (#53): экран 5 бота — календарь готов (docs/screens/05-calendar-ready.md, D16, D22, D27).

Справочник — фикстуры backend/tests/fixtures: test_quarterly (25-е после квартала, без
переноса, всем) и test_yearly (28 декабря, УСН). «Сейчас» — 29.09.2026 (см. test_nds_answer).
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.bot.handlers import calendar_ready, common
from app.calendar import loader
from app.calendar.types import ReferenceFileError, WorkdayCalendar
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Notification, Profile, UserObligation
from app.core.texts import t
from tests.test_nds_answer import (
    NOW,
    USER_ID,
    events,
    get_profile,
    onboard,
    payloads,
    press,
    run,
)

pytestmark = pytest.mark.usefixtures("fixture_reference")

BOT_USERNAME = "pareto_calendar_bot"

# Что сборка должна положить в БД для «УСН 6%, до 10 млн, без сотрудников» 29.09.2026:
# до 31.12.2027 (D22). Даты посчитаны по фикстурам руками.
EXPECTED_ITEMS = {
    ("test_quarterly", date(2026, 10, 25)),
    ("test_quarterly", date(2027, 1, 25)),
    ("test_quarterly", date(2027, 4, 25)),
    ("test_quarterly", date(2027, 7, 25)),
    ("test_quarterly", date(2027, 10, 25)),
    ("test_yearly", date(2026, 12, 28)),
    ("test_yearly", date(2027, 12, 28)),
}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Подменяемое «сейчас»: clock["now"] можно сдвигать внутри теста."""
    state = {"now": NOW}
    monkeypatch.setattr(common, "now", lambda: state["now"])
    return state


@pytest.fixture(autouse=True)
def bot_username(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT_USERNAME)


def ready(count: int, count_word: str, next_date: str, next_title: str) -> str:
    return t(
        "calendar_ready.body",
        count=count,
        count_word=count_word,
        next_date=next_date,
        next_title=next_title,
    )


READY = ready(2, "события", "25 октября", "тестовое квартальное обязательство")


async def items() -> set[tuple[str, date]]:
    async with SessionLocal() as s:
        rows = await s.execute(select(UserObligation.obligation_id, UserObligation.due_date))
        return {tuple(row) for row in rows}


async def count(model) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model))


# --- полный путь -------------------------------------------------------------------------


async def test_full_path_from_start_to_ready_calendar(fake_max, clock):
    screen_3 = await onboard(fake_max, "lt10", "usn6")
    assert payloads(screen_3) == [["calendar:build", "nds:why"]]
    assert await items() == set()  # до нажатия календарь не собран

    clock["now"] = NOW + timedelta(seconds=70)
    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == READY
    assert msg["attachments"] == [
        {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "open_app", "text": "Открыть календарь", "web_app": BOT_USERNAME}]
                ]
            },
        }
    ]
    assert await items() == EXPECTED_ITEMS
    assert await count(Notification) > 0
    profile = await get_profile()
    assert profile.calendar_built_at is not None
    assert await events("calendar_built") == [
        {"items_count": 2, "seconds_since_start": 70, "rebuild": False}
    ]
    assert fake_max.typing == [1001]  # «печатает» — правило «Грузится»


async def test_build_from_why_screen(fake_max):
    await onboard(fake_max)
    why = await run(fake_max, press("nds:why"))
    assert payloads(why)[-1] == ["calendar:build"]

    msg = await run(fake_max, press("calendar:build"))
    assert msg["text"] == READY


async def test_repeat_build_is_idempotent_and_shows_screen_again(fake_max):
    await onboard(fake_max)
    first = await run(fake_max, press("calendar:build"))
    obligations, notifications = await count(UserObligation), await count(Notification)

    second = await run(fake_max, press("calendar:build"))  # старая кнопка или двойное нажатие

    assert second["text"] == first["text"] == READY
    assert await count(UserObligation) == obligations == len(EXPECTED_ITEMS)
    assert await count(Notification) == notifications
    assert [e["rebuild"] for e in await events("calendar_built")] == [False, True]


async def test_same_update_delivered_twice_builds_once(fake_max):
    await onboard(fake_max)
    update = press("calendar:build")
    sent_before = len(fake_max.sent)

    await run(fake_max, update, update)

    assert len(fake_max.sent) == sent_before + 1
    assert len(await events("calendar_built")) == 1


# --- содержание экрана 5 -------------------------------------------------------------------


async def test_employees_add_d16_line(fake_max):
    await onboard(fake_max, employees="yes")
    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == READY + "\n\n" + t("calendar_ready.no_employee_items")


@pytest.mark.parametrize(
    ("tz", "expected"),
    [
        # 25.10.2026 18:00 по Москве — срок 25 октября ещё сегодня
        ("Europe/Moscow", READY),
        # во Владивостоке уже 26 октября: ближайшее — 28 декабря, до конца года одно событие
        (
            "Asia/Vladivostok",
            ready(1, "событие", "28 декабря", "тестовое годовое обязательство"),
        ),
    ],
)
async def test_nearest_and_count_in_user_timezone(fake_max, clock, tz, expected):
    await onboard(fake_max, tz=tz)
    clock["now"] = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)

    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == expected


async def test_empty_calendar(fake_max, fixture_reference, monkeypatch):
    catalog = replace(fixture_reference.catalog, obligations=())
    monkeypatch.setattr(
        loader, "get_reference", lambda: replace(fixture_reference, catalog=catalog)
    )
    await onboard(fake_max, employees="yes")

    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == t("calendar_ready.empty") + "\n\n" + t("calendar_ready.no_employee_items")
    assert payloads(msg) == [[None]]  # только «Открыть календарь», «Настроек» нет
    assert await events("calendar_built") == [
        {"items_count": 0, "seconds_since_start": 0, "rebuild": False}
    ]


async def test_without_bot_username_no_open_app_button(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")
    await onboard(fake_max)

    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == READY
    assert msg["attachments"] is None


# --- ошибки и старые кнопки ----------------------------------------------------------------


async def test_reference_unavailable_then_retry(fake_max, monkeypatch):
    await onboard(fake_max)
    real = loader.get_reference

    def broken():
        raise ReferenceFileError("нет файла obligations.yaml")

    monkeypatch.setattr(loader, "get_reference", broken)
    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == t("common.error")
    assert payloads(msg) == [["calendar:build"]]
    assert await events("error") == [{"where": "calendar_build", "kind": "reference_missing"}]
    assert await items() == set()
    profile = await get_profile()
    assert (profile.income_band, profile.regime, profile.calendar_built_at) == (
        "lt10",
        "usn6",
        None,
    )

    monkeypatch.setattr(loader, "get_reference", real)
    msg = await run(fake_max, press("calendar:build"))  # «Повторить»

    assert msg["text"] == READY
    assert await items() == EXPECTED_ITEMS


async def test_missing_year_in_workdays_is_error(fake_max, fixture_reference, monkeypatch):
    workdays = WorkdayCalendar(years={2026: fixture_reference.workdays.years[2026]})
    monkeypatch.setattr(
        loader, "get_reference", lambda: replace(fixture_reference, workdays=workdays)
    )
    await onboard(fake_max)

    msg = await run(fake_max, press("calendar:build"))

    assert msg["text"] == t("common.error")
    assert payloads(msg) == [["calendar:build"]]
    assert await events("error") == [{"where": "calendar_build", "kind": "missing_year"}]
    assert await items() == set()  # полусборки нет


async def test_old_build_button_with_incomplete_profile_asks_question(fake_max):
    await onboard(fake_max)
    msg = await run(fake_max, press("start:check"), press("calendar:build"))

    assert msg["text"] == t("onboarding.q1", income_year=2025)
    assert await items() == set()
    assert await events("calendar_built") == []


async def test_start_after_build_shows_already_built(fake_max):
    await onboard(fake_max)
    await run(fake_max, press("calendar:build"))

    msg = await run(fake_max, press("start:retry"))

    assert msg["text"] == t("start.already_built")


# --- форма ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "word"),
    [
        (1, "событие"),
        (2, "события"),
        (4, "события"),
        (5, "событий"),
        (11, "событий"),
        (12, "событий"),
        (21, "событие"),
        (23, "события"),
        (0, "событий"),
    ],
)
def test_count_word(n, word):
    assert calendar_ready.count_word(n) == word


def test_format_day_shows_year_only_if_not_current():
    today = date(2026, 9, 29)
    assert calendar_ready.format_day(date(2026, 10, 28), today) == "28 октября"
    assert calendar_ready.format_day(date(2027, 1, 25), today) == "25 января 2027"


def test_screen_5_button_fits_20_chars():
    (board,) = calendar_ready.ready_attachments()
    for row in board["payload"]["buttons"]:
        assert 1 <= len(row) <= 2
        for button in row:
            assert len(button["text"]) <= 20
            assert not button["text"].startswith("[")


async def test_profile_row_is_single(fake_max):
    await onboard(fake_max)
    await run(fake_max, press("calendar:build"), press("calendar:build"))

    assert await count(Profile) == 1
    assert (await get_profile()).user_id == USER_ID
