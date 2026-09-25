"""T8b (#62): экран 10 бота — «не понял», «не нашёл дату», «сервис не ответил».

Помощники и замороженное «сейчас» (23.09.2026, среда) — из test_task_chat.
"""

from datetime import date

import pytest
import yaml

from app.bot.dispatcher import process_update
from app.bot.handlers import common, fallback, task_chat
from app.bot.router import router
from app.calendar import reminders as rem
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import DialogState
from app.core.texts import t
from tests.conftest import ROOT, load_update
from tests.test_task_chat import (
    BOT_NAME,
    NOW,
    USER_ID,
    buttons,
    confirm_text,
    draft_fields,
    events,
    labels,
    no_remind_text,
    onboarded,
    payload_of,
    press,
    state_data,
    tap,
    tasks,
    write,
)

pytestmark = pytest.mark.usefixtures("fixture_reference")

SUPPORT = "https://max.ru/support"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(common, "now", lambda: NOW)


@pytest.fixture
def bot_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT_NAME)


@pytest.fixture
def no_bot_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")


@pytest.fixture
def support(monkeypatch):
    monkeypatch.setattr(get_settings(), "support_url", SUPPORT)


@pytest.fixture
def no_support(monkeypatch):
    monkeypatch.setattr(get_settings(), "support_url", "")


@pytest.fixture
def broken_save(monkeypatch):
    """Сохранение задачи падает, пока `state["fail"]` истинно."""
    state = {"fail": True}
    real = rem.sync_task_notification

    async def sync(*args, **kwargs):
        if state["fail"]:
            raise RuntimeError("база недоступна")
        return await real(*args, **kwargs)

    monkeypatch.setattr(rem, "sync_task_notification", sync)
    return state


def fallback_cases(evs: list[tuple[str, dict]]) -> list[str]:
    return [props["case"] for name, props in evs if name == "fallback_shown"]


# --- не понял ------------------------------------------------------------------------------


async def test_unknown_text_shows_calendar_and_about(fake_max, bot_name, no_support):
    await onboarded()
    await process_update(write("привет"), fake_max)

    msg = fake_max.sent[-1]
    assert msg["text"] == t("fallback.unknown")
    assert labels(msg) == [[t("fallback.btn_open"), t("fallback.btn_about")]]
    assert buttons(msg)[0][0]["type"] == "open_app"
    assert buttons(msg)[0][1]["payload"] == "about:open"
    assert fallback_cases(await events()) == ["unknown"]


async def test_unknown_callback_is_unknown(fake_max, no_bot_name, no_support):
    await onboarded()
    await process_update(press("nothing:here"), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")
    assert labels(fake_max.sent[-1]) == [[t("fallback.btn_about")]]


async def test_third_unknown_in_a_row_offers_write_to_us(fake_max, no_bot_name, support):
    await onboarded()
    for text in ("привет", "ага", "ну"):
        await process_update(write(text), fake_max)

    first, second, third = fake_max.sent
    assert first["text"] == second["text"] == t("fallback.unknown")
    assert third["text"] == f"{t('fallback.unknown')}\n{t('fallback.unknown_3')}"
    assert labels(third) == [[t("fallback.btn_about")], [t("fallback.btn_write")]]
    assert buttons(third)[1][0] == {"type": "link", "text": t("fallback.btn_write"), "url": SUPPORT}


async def test_without_support_url_no_write_button(fake_max, no_bot_name, no_support):
    await onboarded()
    for text in ("привет", "ага", "ну", "эх"):
        await process_update(write(text), fake_max)

    for msg in fake_max.sent:
        assert msg["text"] == t("fallback.unknown")
        assert labels(msg) == [[t("fallback.btn_about")]]


async def test_successful_action_resets_unknown_counter(fake_max, no_bot_name, support):
    await onboarded()
    await process_update(write("привет"), fake_max)
    await process_update(write("ага"), fake_max)
    await process_update(write("/about"), fake_max)  # успешное действие
    await process_update(write("ну"), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")
    assert "fb_unknown" in await state_data()


# --- не нашёл дату -------------------------------------------------------------------------


async def test_no_date_keeps_title_and_offers_dates(fake_max, bot_name, miniapp_api):
    await onboarded()
    await process_update(write("оплатить аренду"), fake_max)

    msg = fake_max.sent[-1]
    assert msg["text"] == t("fallback.no_date", title="Оплатить аренду")
    assert labels(msg) == [
        [t("fallback.btn_tomorrow"), t("fallback.btn_week")],
        [t("fallback.btn_pick")],
    ]
    pick = buttons(msg)[1][0]
    assert (pick["type"], pick["payload"], pick["web_app"]) == ("open_app", "task_draft", BOT_NAME)

    names = [name for name, _ in await events()]
    assert names == ["date_not_parsed", "fallback_shown"]
    assert fallback_cases(await events()) == ["no_date"]

    # «Выбрать дату»: форма 17 получает название (дата — завтра, её выбирают в форме)
    body = (await miniapp_api.request("GET", "/api/me", USER_ID)).json()
    assert body["draft"] == {"title": "Оплатить аренду", "due_date": "2026-09-24"}


async def test_pick_date_hidden_without_bot_username(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду"), fake_max)

    assert labels(fake_max.sent[-1]) == [[t("fallback.btn_tomorrow"), t("fallback.btn_week")]]


@pytest.mark.parametrize(
    ("payload", "day", "due", "remind"),
    [
        # сейчас 12:00: напоминание «за день в 10:00» для завтра уже прошло (#87)
        (fallback.TOMORROW, "24 сентября", date(2026, 9, 24), False),
        (fallback.WEEK, "30 сентября", date(2026, 9, 30), True),
    ],
)
async def test_tomorrow_and_week_go_to_confirm(fake_max, no_bot_name, payload, day, due, remind):
    await onboarded()
    await process_update(write("оплатить аренду"), fake_max)
    await process_update(press(payload), fake_max)

    expected = (confirm_text if remind else no_remind_text)("Оплатить аренду", day)
    assert fake_max.sent[-1]["text"] == expected
    assert "task_title" not in await state_data()

    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    [task] = await tasks()
    assert (task.title, task.due_date) == ("Оплатить аренду", due)


async def test_save_button_does_not_save_no_date_draft(fake_max, no_bot_name):
    """Пока висит «не нашёл дату», черновик с завтрашним днём — только для формы 17."""
    await onboarded()
    await process_update(write("оплатить аренду"), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)

    assert await tasks() == []
    assert fake_max.sent[-1]["text"] == t("fallback.unknown")


async def test_stale_tomorrow_button_is_unknown(fake_max, no_bot_name):
    await onboarded()
    await process_update(press(fallback.TOMORROW), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")


# --- сервис не ответил ---------------------------------------------------------------------


async def test_failed_save_shows_service_and_retry_saves_once(fake_max, no_bot_name, broken_save):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    save = payload_of(fake_max.sent[-1], "task.btn_save")
    await process_update(press(save), fake_max)

    msg = fake_max.sent[-1]
    assert msg["text"] == t("fallback.service")
    retry = {"type": "callback", "text": t("fallback.btn_retry"), "payload": fallback.RETRY}
    assert buttons(msg) == [[retry]]
    assert await tasks() == []
    data = await state_data()
    assert draft_fields(data) == {"title": "Оплатить аренду", "due_date": "2026-11-05"}
    assert data["last_action"] == {
        "update_type": "message_callback",
        "text": None,
        "payload": save,
    }

    broken_save["fail"] = False
    await process_update(press(fallback.RETRY), fake_max)

    assert fake_max.sent[-1]["text"] == t(
        "task.saved", count=1, count_word=t("calendar_ready.count_one")
    )
    assert len(await tasks()) == 1
    data = await state_data()
    assert "last_action" not in data and "fb_service" not in data

    # «Повторить» из старого сообщения: повторять нечего, вторая задача не появится
    await process_update(press(fallback.RETRY), fake_max)
    assert len(await tasks()) == 1
    assert fake_max.sent[-1]["text"] == t("fallback.unknown")


async def test_second_failure_in_a_row_shows_service_2(fake_max, no_bot_name, broken_save):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    save = payload_of(fake_max.sent[-1], "task.btn_save")
    await process_update(press(save), fake_max)
    await process_update(press(fallback.RETRY), fake_max)

    assert [m["text"] for m in fake_max.sent[-2:]] == [
        t("fallback.service"),
        t("fallback.service_2"),
    ]
    assert labels(fake_max.sent[-1]) == [[t("fallback.btn_retry")]]
    # повтор упал — упавшее действие осталось прежним, а не «Повторить»
    assert (await state_data())["last_action"]["payload"] == save
    assert fallback_cases(await events()) == ["service", "service"]


async def test_success_resets_service_counter(fake_max, no_bot_name, broken_save):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    save = payload_of(fake_max.sent[-1], "task.btn_save")
    await process_update(press(save), fake_max)
    await process_update(write("привет"), fake_max)  # успешное действие
    await process_update(press(save), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.service")


async def test_retry_repeats_failed_text_message(fake_max, no_bot_name, monkeypatch):
    """«Повторить» после упавшего сообщения — тот же разбор того же текста."""
    await onboarded()
    real = task_chat.propose
    state = {"fail": True}

    async def propose(*args, **kwargs):
        if state["fail"]:
            raise RuntimeError("сбой")
        return await real(*args, **kwargs)

    monkeypatch.setattr(task_chat, "propose", propose)
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    assert fake_max.sent[-1]["text"] == t("fallback.service")

    state["fail"] = False
    await process_update(press(fallback.RETRY), fake_max)
    assert fake_max.sent[-1]["text"] == confirm_text("Оплатить аренду", "5 ноября")


async def test_reply_goes_out_even_if_failure_record_fails(fake_max, monkeypatch):
    """Запись сбоя упала — ответ `service` всё равно уходит."""

    async def boom(ctx):
        raise RuntimeError("обработчик упал")

    async def resolve(ctx):
        return boom

    async def record(ctx):
        raise RuntimeError("и запись сбоя тоже")

    monkeypatch.setattr(router, "resolve", resolve)
    monkeypatch.setattr(fallback, "_record_failure", record)
    await process_update(write("привет"), fake_max)

    assert [m["text"] for m in fake_max.sent] == [t("fallback.service")]


async def test_service_update_without_state_row_is_not_created(fake_max):
    """after_handler не заводит DialogState служебным апдейтам (bot_stopped и т.п.)."""
    await process_update(load_update("bot_stopped"), fake_max)
    async with SessionLocal() as s:
        assert await s.get(DialogState, USER_ID) is None


# --- тексты --------------------------------------------------------------------------------


def test_texts_have_no_error_codes_or_server_word():
    """«Ни в одном тексте нет кода ошибки и слова „сервер“» — разделы экранов 9 и 10."""
    texts = yaml.safe_load((ROOT / "content" / "texts.yaml").read_text(encoding="utf-8"))
    assert "errors" not in texts
    for section in ("task", "fallback"):
        for key, value in texts[section].items():
            assert "сервер" not in value.lower(), f"{section}.{key}"
            assert "error" not in value.lower(), f"{section}.{key}"
            assert not any(code in value for code in ("500", "502", "503", "504")), key
