"""T9 (#54): экран 11 «О сервисе» и команда /about.

Справочник — фикстура backend/tests/fixtures (version "2026-01-01" → «01.01.2026»).
"""

import itertools
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.calendar import loader
from app.calendar.types import ReferenceFileError
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Event, Profile
from app.core.texts import t
from tests.conftest import load_update

pytestmark = pytest.mark.usefixtures("fixture_reference")

USER_ID = 42
LAST_CHECKED = "01.01.2026"
_seq = itertools.count(1)


def press(payload: str) -> dict:
    n = next(_seq)
    update = load_update("callback_start_check")
    update["timestamp"] += n
    update["callback"] = dict(update["callback"], payload=payload, callback_id=f"cb-about-{n}")
    return update


def write(text: str) -> dict:
    n = next(_seq)
    update = load_update("message_created")
    update["timestamp"] += n
    update["message"] = dict(update["message"], body={"mid": f"mid-about-{n}", "text": text})
    return update


def keyboard(message: dict) -> list[list[dict]]:
    attachments = message["attachments"] or []
    return attachments[0]["payload"]["buttons"] if attachments else []


def payloads(message: dict) -> list[list[str | None]]:
    return [[b.get("payload") for b in row] for row in keyboard(message)]


async def run(fake_max, *updates: dict) -> dict:
    for update in updates:
        await process_update(update, fake_max)
    return fake_max.sent[-1]


async def get_profile() -> Profile | None:
    async with SessionLocal() as s:
        return await s.get(Profile, USER_ID)


async def mark_built() -> None:
    async with SessionLocal() as s:
        profile = await s.get(Profile, USER_ID)
        if profile is None:
            profile = Profile(user_id=USER_ID, timezone="Europe/Moscow")
            s.add(profile)
        profile.calendar_built_at = datetime(2026, 9, 1, tzinfo=UTC)
        await s.commit()


async def events(name: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Event.props).where(Event.name == name).order_by(Event.id))
        return list(rows)


def body_with_date() -> str:
    return t("about.body", last_checked=LAST_CHECKED)


# --- содержимое и повод входа -----------------------------------------------------------------


async def test_about_command_shows_body_with_reference_date(fake_max):
    msg = await run(fake_max, write("/about"))

    assert msg["text"].startswith(body_with_date())
    assert await events("about_opened") == [{}]


async def test_about_button_from_screen_1_shows_same_screen(fake_max):
    msg = await run(fake_max, press("about:open"))

    assert msg["text"].startswith(body_with_date())


async def test_about_works_mid_onboarding(fake_max):
    """«/about работает в любой момент, в том числе посреди онбординга» — Готово когда."""
    await run(fake_max, press("start:check"), press("onb:1:20_60"))

    msg = await run(fake_max, write("/about"))

    assert msg["text"].startswith(body_with_date())


# --- ссылки D13 ----------------------------------------------------------------------------


async def test_privacy_line_shown_and_write_button_is_callback_when_urls_set(
    fake_max, monkeypatch
):
    """D13 после «Написать нам в боте»: SUPPORT_URL ботом не используется, ссылки нет."""
    monkeypatch.setattr(get_settings(), "support_url", "https://max.ru/support")
    monkeypatch.setattr(get_settings(), "privacy_url", "https://max.ru/privacy")

    msg = await run(fake_max, write("/about"))

    assert t("about.privacy", privacy_url="https://max.ru/privacy") in msg["text"]
    assert all(b.get("type") != "link" for row in keyboard(msg) for b in row)
    assert payloads(msg) == [["start:check", "feedback:start:about"]]


async def test_privacy_line_hidden_write_button_shown_without_urls(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "support_url", "")
    monkeypatch.setattr(get_settings(), "privacy_url", "")

    msg = await run(fake_max, write("/about"))

    assert "Как мы обрабатываем данные" not in msg["text"]
    assert all(b.get("type") != "link" for row in keyboard(msg) for b in row)
    write_buttons = [b for row in keyboard(msg) for b in row if b["text"] == t("about.btn_write")]
    assert write_buttons == [
        {"type": "callback", "text": t("about.btn_write"), "payload": "feedback:start:about"}
    ]


# --- вторая кнопка: check или open -----------------------------------------------------------


async def test_calendar_not_built_shows_check_button(fake_max):
    msg = await run(fake_max, write("/about"))

    assert payloads(msg) == [["start:check", "feedback:start:about"]]


async def test_calendar_built_shows_open_app_button(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "pareto_calendar_bot")
    await run(fake_max, press("start:check"))  # создаёт профиль
    await mark_built()

    msg = await run(fake_max, write("/about"))

    [[open_btn, write_btn]] = keyboard(msg)
    assert write_btn["payload"] == "feedback:start:about"
    assert open_btn == {
        "type": "open_app",
        "text": t("about.btn_open"),
        "web_app": "pareto_calendar_bot",
    }


async def test_calendar_built_without_bot_username_shows_only_write_button(
    fake_max, monkeypatch
):
    """Без MAX_BOT_USERNAME «Открыть календарь» нет, но «Написать нам» остаётся."""
    monkeypatch.setattr(get_settings(), "max_bot_username", "")
    await run(fake_max, press("start:check"))
    await mark_built()

    msg = await run(fake_max, write("/about"))

    assert payloads(msg) == [["feedback:start:about"]]


# --- справочник недоступен -------------------------------------------------------------------


async def test_reference_unavailable_shows_body_without_date_no_error(
    fake_max, monkeypatch, caplog
):
    real = loader.get_reference

    def broken():
        raise ReferenceFileError("нет файла obligations.yaml")

    monkeypatch.setattr(loader, "get_reference", broken)

    msg = await run(fake_max, write("/about"))

    assert msg["text"].startswith(t("about.body_no_reference"))
    assert msg["text"] != t("common.error")
    assert "нет файла obligations.yaml" in caplog.text
    # кнопка всё равно есть: экран не переходит в состояние ошибки
    assert payloads(msg) == [["start:check", "feedback:start:about"]]

    monkeypatch.setattr(loader, "get_reference", real)
