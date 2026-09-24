"""T8b (#62): экран 9 бота — своя задача текстом.

«Сейчас» заморожено: 23.09.2026 12:00 по Москве (среда). Справочники — фикстуры.
"""

import itertools
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.bot.handlers import common, fallback, task_chat
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import DialogState, Event, Notification, Profile, Task, User, UserObligation
from app.core.texts import t
from tests.conftest import load_update

pytestmark = pytest.mark.usefixtures("fixture_reference")

USER_ID = 42
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)  # 12:00 по Москве, среда
TODAY = date(2026, 9, 23)
BOT_NAME = "pareto_calendar_bot"

_seq = itertools.count(1)


# --- помощники ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(common, "now", lambda: state["now"])
    return state


@pytest.fixture
def bot_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT_NAME)


@pytest.fixture
def no_bot_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")


def press(payload: str) -> dict:
    n = next(_seq)
    update = load_update("callback_start_check")
    update["timestamp"] += n
    update["callback"] = dict(update["callback"], payload=payload, callback_id=f"cb-task-{n}")
    return update


def write(text: str) -> dict:
    n = next(_seq)
    update = load_update("message_created")
    update["timestamp"] += n
    update["message"] = dict(update["message"], body={"mid": f"mid-task-{n}", "text": text})
    return update


async def onboarded(user_id: int = USER_ID, **fields) -> None:
    """Пользователь, прошедший онбординг (ответы на вопросы 1–3, пояс Москва)."""
    values = {
        "income_band": "lt10",
        "regime": "usn6",
        "has_employees": False,
        "timezone": "Europe/Moscow",
        "started_at": NOW,
    }
    values.update(fields)
    async with SessionLocal() as s:
        s.add(User(user_id=user_id, name="Борис"))
        await s.flush()
        s.add(Profile(user_id=user_id, **values))
        await s.commit()


async def state_data(user_id: int = USER_ID) -> dict:
    async with SessionLocal() as s:
        row = await s.get(DialogState, user_id)
        return dict(row.data or {}) if row else {}


async def tasks(user_id: int = USER_ID) -> list[Task]:
    async with SessionLocal() as s:
        return list((await s.execute(select(Task).where(Task.user_id == user_id))).scalars())


async def events() -> list[tuple[str, dict]]:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Event.name, Event.props).order_by(Event.id))).all()
    return [(r.name, r.props) for r in rows]


def buttons(message: dict) -> list[list[dict]]:
    return message["attachments"][0]["payload"]["buttons"]


def labels(message: dict) -> list[list[str]]:
    return [[b["text"] for b in row] for row in buttons(message)]


def confirm_text(title: str, day: str) -> str:
    return t("task.confirm", title=title, date=day, remind_when=t("task.remind_day"), hour=10)


# --- разбор: каждый формат доходит до confirm ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "day", "iso"),
    [
        ("оплатить аренду 5 ноября", "5 ноября", "2026-11-05"),
        ("оплатить аренду 5 нояб", "5 ноября", "2026-11-05"),
        ("оплатить аренду 05.11", "5 ноября", "2026-11-05"),
        ("оплатить аренду 5.11.2026", "5 ноября", "2026-11-05"),
        ("оплатить аренду сегодня", "23 сентября", "2026-09-23"),
        ("оплатить аренду завтра", "24 сентября", "2026-09-24"),
        ("оплатить аренду послезавтра", "25 сентября", "2026-09-25"),
        ("оплатить аренду в понедельник", "28 сентября", "2026-09-28"),
        ("оплатить аренду в ср", "30 сентября", "2026-09-30"),  # сегодняшний день не считается
        ("оплатить аренду 10 марта", "10 марта 2027", "2027-03-10"),  # год — если не текущий
    ],
)
async def test_every_date_format_reaches_confirm(fake_max, bot_name, text, day, iso):
    await onboarded()
    await process_update(write(text), fake_max)

    assert [m["text"] for m in fake_max.sent] == [confirm_text("Оплатить аренду", day)]
    assert (await state_data())["task_draft"] == {"title": "Оплатить аренду", "due_date": iso}
    assert await tasks() == []  # до «Сохранить» ничего не создано


async def test_confirm_keyboard_save_then_edit_and_cancel(fake_max, bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    rows = buttons(fake_max.sent[0])
    assert labels(fake_max.sent[0]) == [
        [t("task.btn_save")],
        [t("task.btn_edit"), t("task.btn_cancel")],
    ]
    assert rows[0][0]["payload"] == task_chat.SAVE
    edit = rows[1][0]
    assert edit["type"] == "open_app"
    assert edit["payload"] == "task_draft"
    assert edit["web_app"] == BOT_NAME
    assert rows[1][1]["payload"] == task_chat.CANCEL


async def test_edit_button_hidden_without_bot_username(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    assert labels(fake_max.sent[0]) == [[t("task.btn_save")], [t("task.btn_cancel")]]


async def test_edit_draft_is_read_by_api_me(fake_max, bot_name, miniapp_api):
    """«Изменить» открывает форму 17: `/api/me` отдаёт черновик, записанный ботом."""
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    body = (await miniapp_api.request("GET", "/api/me", USER_ID)).json()
    assert body["draft"] == {"title": "Оплатить аренду", "due_date": "2026-11-05"}


# --- сохранение ----------------------------------------------------------------------------


async def test_save_creates_task_notification_and_event(fake_max, bot_name):
    await onboarded()
    async with SessionLocal() as s:
        # в счёт идут: будущее и сегодняшнее невыполненные; не идут: выполненное и прошедшее
        for due, done_at in (
            (date(2026, 10, 28), None),
            (TODAY, None),
            (date(2026, 10, 1), NOW),
            (date(2026, 9, 1), None),
        ):
            s.add(
                UserObligation(
                    user_id=USER_ID,
                    obligation_id=f"ob-{due.isoformat()}",
                    rule_version=1,
                    original_date=due,
                    due_date=due,
                    done_at=done_at,
                )
            )
        await s.commit()

    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)

    [task] = await tasks()
    assert (task.title, task.due_date) == ("Оплатить аренду", date(2026, 11, 5))
    assert (task.remind_offset_days, task.remind_hour) == (1, 10)
    assert task.done_at is None and task.deleted_at is None

    async with SessionLocal() as s:
        [n] = (await s.execute(select(Notification))).scalars().all()
    assert (n.item_type, n.item_id, n.kind, n.status) == ("task", task.id, "task", "pending")
    # за день в 10:00 по Москве = 07:00 UTC
    assert n.send_at.replace(tzinfo=UTC) == datetime(2026, 11, 4, 7, 0, tzinfo=UTC)

    assert ("task_created", {"source": "chat", "remind_offset": 1}) in await events()
    # сегодняшнее обязательство, будущее обязательство и новая задача
    saved = fake_max.sent[-1]
    assert saved["text"] == t("task.saved", count=3, count_word=t("calendar_ready.count_few"))
    assert labels(saved) == [[t("task.btn_open")]]
    assert buttons(saved)[0][0]["type"] == "open_app"
    data = await state_data()
    assert "task_draft" not in data and "task_title" not in data


async def test_saved_task_is_in_calendar_as_custom(fake_max, no_bot_name, miniapp_api):
    """Задача из чата видна мини-аппу (список и сетка месяца) с категорией «Своё»."""
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)

    r = await miniapp_api.request("GET", "/api/calendar?from=2026-11-01&to=2026-11-30", USER_ID)
    assert r.status_code == 200
    got = [(i["type"], i["title"], i["category"], i["due_date"]) for i in r.json()]
    assert got == [("task", "Оплатить аренду", "custom", "2026-11-05")]


async def test_saved_without_bot_username_has_no_button(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду завтра"), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)

    saved = fake_max.sent[-1]
    assert saved["text"] == t("task.saved", count=1, count_word=t("calendar_ready.count_one"))
    assert saved["attachments"] is None


async def test_second_press_on_save_does_not_duplicate(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)

    assert len(await tasks()) == 1
    assert fake_max.sent[-1]["text"] == t("fallback.unknown")


async def test_cancel_saves_nothing(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    await process_update(press(task_chat.CANCEL), fake_max)

    assert fake_max.sent[-1]["text"] == t("task.cancelled")
    assert await tasks() == []
    assert "task_draft" not in await state_data()


# --- past_date и duplicate -----------------------------------------------------------------


async def test_past_date_with_explicit_year_offers_next_year(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5.09.2026"), fake_max)

    msg = fake_max.sent[-1]
    assert msg["text"] == t("task.past_date", date="5 сентября", next_year_date="5 сентября 2027")
    assert labels(msg) == [[t("task.btn_yes"), t("task.btn_cancel")]]

    await process_update(press(task_chat.YES), fake_max)
    assert fake_max.sent[-1]["text"] == confirm_text("Оплатить аренду", "5 сентября 2027")
    assert (await state_data())["task_draft"]["due_date"] == "2027-09-05"

    await process_update(press(task_chat.SAVE), fake_max)
    [task] = await tasks()
    assert task.due_date == date(2027, 9, 5)


async def test_save_of_past_draft_is_refused(fake_max, no_bot_name):
    """«Сохранить» на past_date не бывает, но и старая кнопка не сохранит дату в прошлом."""
    await onboarded()
    await process_update(write("оплатить аренду 5.09.2026"), fake_max)
    await process_update(press(task_chat.SAVE), fake_max)

    assert await tasks() == []
    assert fake_max.sent[-1]["text"] == fake_max.sent[0]["text"]  # снова past_date


async def test_duplicate_offers_add_anyway(fake_max, no_bot_name):
    await onboarded()
    async with SessionLocal() as s:
        s.add(Task(user_id=USER_ID, title="Оплатить аренду", due_date=date(2026, 11, 5)))
        await s.commit()

    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    msg = fake_max.sent[-1]
    assert msg["text"] == t("task.duplicate", date="5 ноября")
    assert labels(msg) == [[t("task.btn_add_anyway"), t("task.btn_cancel")]]

    await process_update(press(task_chat.ANYWAY), fake_max)
    assert len(await tasks()) == 2
    assert fake_max.sent[-1]["text"] == t(
        "task.saved", count=2, count_word=t("calendar_ready.count_few")
    )


async def test_deleted_task_is_not_a_duplicate(fake_max, no_bot_name):
    await onboarded()
    async with SessionLocal() as s:
        s.add(
            Task(
                user_id=USER_ID,
                title="Оплатить аренду",
                due_date=date(2026, 11, 5),
                deleted_at=NOW,
            )
        )
        await s.commit()

    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    assert fake_max.sent[-1]["text"] == confirm_text("Оплатить аренду", "5 ноября")


async def test_only_date_without_title_is_not_saved(fake_max, no_bot_name):
    """Название пустое — `task.empty_title` (TODO в спеке) и кнопки случая «не понял»."""
    await onboarded()
    await process_update(write("5 ноября"), fake_max)

    assert fake_max.sent[-1]["text"] == t("task.empty_title")
    assert labels(fake_max.sent[-1]) == [[t("fallback.btn_about")]]
    assert "task_draft" not in await state_data()


# --- онбординг не перехватывается ----------------------------------------------------------


async def test_text_before_onboarding_gets_use_buttons(fake_max):
    async with SessionLocal() as s:
        s.add(User(user_id=USER_ID, name="Борис"))
        await s.flush()
        s.add(Profile(user_id=USER_ID, timezone="Europe/Moscow", started_at=NOW))
        await s.commit()

    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    assert fake_max.sent[-1]["text"] == t("onboarding.use_buttons")
    assert labels(fake_max.sent[-1]) == [[t("start.btn_check"), t("start.btn_about")]]
    assert await tasks() == []
    assert "task_draft" not in await state_data()


async def test_text_during_onboarding_question_is_not_intercepted(fake_max):
    """Шаг онбординга открыт: текст обрабатывает экран 2, а не разбор задачи."""
    await onboarded()
    async with SessionLocal() as s:
        s.add(DialogState(user_id=USER_ID, state="onb:q2", data={}))
        await s.commit()

    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    assert fake_max.sent[-1]["text"] == t("onboarding.use_buttons")
    assert fake_max.sent[-1]["attachments"][0]["payload"]["buttons"][0][0]["payload"].startswith(
        "onb:2:"
    )
    assert "task_draft" not in await state_data()


def test_task_draft_has_exactly_two_keys():
    """Контракт с `/api/me`: ровно title и due_date в ISO."""
    assert fallback.draft("Аренда", date(2026, 11, 5)) == {
        "title": "Аренда",
        "due_date": "2026-11-05",
    }
