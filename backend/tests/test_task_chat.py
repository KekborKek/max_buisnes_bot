"""T8b (#62): экран 9 бота — своя задача текстом.

«Сейчас» заморожено: 23.09.2026 12:00 по Москве (среда). Справочники — фикстуры.
"""

import itertools
import re
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.bot.handlers import common, fallback, task_chat
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import DialogState, Event, Notification, Profile, Task, User, UserObligation
from app.core.texts import t
from tests.conftest import load_update

# Черновик без времени в /api/me (#103)
NO_TIME = {"remind_hour": None, "remind_minute": None}

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
    return t("task.confirm", title=title, date=day, remind_when=t("task.remind_day"), time="10:00")


def no_remind_text(title: str, day: str) -> str:
    return t("task.confirm_no_remind", title=title, date=day)


def payload_of(message: dict, text_key: str) -> str:
    """Payload кнопки сообщения по её тексту (`task.btn_save` и т. п.)."""
    [found] = [b["payload"] for row in buttons(message) for b in row if b["text"] == t(text_key)]
    return found


def tap(message: dict, text_key: str) -> dict:
    """Нажатие на кнопку именно этого сообщения — с id его черновика в payload (#87)."""
    return press(payload_of(message, text_key))


def draft_fields(data: dict) -> dict:
    """Поля черновика, которые читает `/api/me` (без `id`)."""
    return {k: v for k, v in data["task_draft"].items() if k != "id"}


# --- разбор: каждый формат доходит до confirm ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "day", "iso", "remind"),
    [
        ("оплатить аренду 5 ноября", "5 ноября", "2026-11-05", True),
        ("оплатить аренду 5 нояб", "5 ноября", "2026-11-05", True),
        ("оплатить аренду 05.11", "5 ноября", "2026-11-05", True),
        ("оплатить аренду 5.11.2026", "5 ноября", "2026-11-05", True),
        # напоминание за день в 10:00 уже прошло (сейчас 12:00) — не обещаем (#87)
        ("оплатить аренду сегодня", "23 сентября", "2026-09-23", False),
        ("оплатить аренду завтра", "24 сентября", "2026-09-24", False),
        ("оплатить аренду послезавтра", "25 сентября", "2026-09-25", True),
        ("оплатить аренду в понедельник", "28 сентября", "2026-09-28", True),
        # сегодняшний день недели не считается
        ("оплатить аренду в ср", "30 сентября", "2026-09-30", True),
        # год — если не текущий
        ("оплатить аренду 10 марта", "10 марта 2027", "2027-03-10", True),
    ],
)
async def test_every_date_format_reaches_confirm(fake_max, bot_name, text, day, iso, remind):
    await onboarded()
    await process_update(write(text), fake_max)

    expected = (confirm_text if remind else no_remind_text)("Оплатить аренду", day)
    assert [m["text"] for m in fake_max.sent] == [expected]
    data = await state_data()
    assert draft_fields(data) == {"title": "Оплатить аренду", "due_date": iso}
    assert isinstance(data["task_draft"]["id"], str) and data["task_draft"]["id"]
    assert await tasks() == []  # до «Сохранить» ничего не создано


async def test_confirm_keyboard_save_then_edit_and_cancel(fake_max, bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    rows = buttons(fake_max.sent[0])
    assert labels(fake_max.sent[0]) == [
        [t("task.btn_save")],
        [t("task.btn_edit"), t("task.btn_cancel")],
    ]
    draft_id = (await state_data())["task_draft"]["id"]
    assert rows[0][0]["payload"] == f"{task_chat.SAVE}:{draft_id}"
    edit = rows[1][0]
    assert edit["type"] == "open_app"
    assert edit["payload"] == f"task_draft_{draft_id}"  # форма 17 со своим черновиком (#96)
    assert edit["web_app"] == BOT_NAME
    assert rows[1][1]["payload"] == f"{task_chat.CANCEL}:{draft_id}"


async def test_edit_button_hidden_without_bot_username(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    assert labels(fake_max.sent[0]) == [[t("task.btn_save")], [t("task.btn_cancel")]]


async def test_edit_draft_is_read_by_api_me(fake_max, bot_name, miniapp_api):
    """«Изменить» открывает форму 17: `/api/me` отдаёт черновик, записанный ботом."""
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)

    body = (await miniapp_api.request("GET", "/api/me", USER_ID)).json()
    assert body["draft"] == {**NO_TIME, "title": "Оплатить аренду", "due_date": "2026-11-05"}


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
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)

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
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)

    r = await miniapp_api.request("GET", "/api/calendar?from=2026-11-01&to=2026-11-30", USER_ID)
    assert r.status_code == 200
    got = [(i["type"], i["title"], i["category"], i["due_date"]) for i in r.json()]
    assert got == [("task", "Оплатить аренду", "custom", "2026-11-05")]


async def test_saved_without_bot_username_has_no_button(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду завтра"), fake_max)
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)

    saved = fake_max.sent[-1]
    assert saved["text"] == t("task.saved", count=1, count_word=t("calendar_ready.count_one"))
    assert saved["attachments"] is None


async def test_second_press_on_save_does_not_duplicate(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    save = payload_of(fake_max.sent[-1], "task.btn_save")
    await process_update(press(save), fake_max)
    await process_update(press(save), fake_max)  # та же кнопка ещё раз

    assert len(await tasks()) == 1
    assert fake_max.sent[-1]["text"] == t("fallback.unknown")


async def test_cancel_saves_nothing(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    await process_update(tap(fake_max.sent[-1], "task.btn_cancel"), fake_max)

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

    await process_update(tap(fake_max.sent[-1], "task.btn_yes"), fake_max)
    assert fake_max.sent[-1]["text"] == confirm_text("Оплатить аренду", "5 сентября 2027")
    assert (await state_data())["task_draft"]["due_date"] == "2027-09-05"

    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    [task] = await tasks()
    assert task.due_date == date(2027, 9, 5)


async def test_save_of_past_draft_is_refused(fake_max, no_bot_name, clock):
    """Черновик пролежал до следующего дня: «Сохранить» не сохранит дату в прошлом."""
    await onboarded()
    await process_update(write("оплатить аренду сегодня"), fake_max)
    clock["now"] = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)  # следующий день
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)

    assert await tasks() == []
    assert fake_max.sent[-1]["text"] == t(
        "task.past_date", date="23 сентября", next_year_date="23 сентября 2027"
    )

    # «Да» на этом past_date — снова confirm, уже на следующий год
    await process_update(tap(fake_max.sent[-1], "task.btn_yes"), fake_max)
    assert fake_max.sent[-1]["text"] == confirm_text("Оплатить аренду", "23 сентября 2027")
    assert (await state_data())["task_draft"]["due_date"] == "2027-09-23"


async def test_duplicate_offers_add_anyway(fake_max, no_bot_name):
    await onboarded()
    async with SessionLocal() as s:
        s.add(Task(user_id=USER_ID, title="Оплатить аренду", due_date=date(2026, 11, 5)))
        await s.commit()

    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    msg = fake_max.sent[-1]
    assert msg["text"] == t("task.duplicate", date="5 ноября")
    assert labels(msg) == [[t("task.btn_add_anyway"), t("task.btn_cancel")]]

    await process_update(tap(fake_max.sent[-1], "task.btn_add_anyway"), fake_max)
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


def test_task_draft_keeps_api_me_fields():
    """Контракт с `/api/me`: title и due_date в ISO не меняются; `id` — новое поле (#87)."""
    assert fallback.draft("Аренда", date(2026, 11, 5)) == {
        "title": "Аренда",
        "due_date": "2026-11-05",
    }
    assert fallback.draft("Аренда", date(2026, 11, 5), "ab12cd34") == {
        "title": "Аренда",
        "due_date": "2026-11-05",
        "id": "ab12cd34",
    }


def test_draft_payload_fits_max_limit():
    """Payload callback-кнопки — до 1024 символов (docs/max-api-notes.md)."""
    for action in (task_chat.SAVE, task_chat.ANYWAY, task_chat.YES, task_chat.CANCEL):
        assert len(task_chat.payload(action, "ab12cd34")) <= 1024


def test_draft_start_param_format():
    """start_param «Изменить» / «Выбрать дату» (#96): латиница, цифры и `_`, как `item_*`.

    Ограничений длины и алфавита payload open_app схема MAX не задаёт (docs/max-api-notes.md).
    """
    draft_id = fallback.new_draft_id()
    assert re.fullmatch(r"[0-9a-f]{8}", draft_id)
    assert fallback.draft_start_param(draft_id) == f"task_draft_{draft_id}"
    assert fallback.draft_start_param(None) == "task_draft"


# --- #87: кнопка старого черновика не сохраняет новый --------------------------------------


async def two_drafts(fake_max) -> tuple[dict, dict]:
    """Confirm A («аренда 30 октября»), затем confirm B («налог 5 ноября»)."""
    await onboarded()
    await process_update(write("оплатить аренду 30 октября"), fake_max)
    first = fake_max.sent[-1]
    await process_update(write("заплатить налог 5 ноября"), fake_max)
    return first, fake_max.sent[-1]


async def test_save_under_old_confirm_does_not_save_new_draft(fake_max, no_bot_name):
    first, second = await two_drafts(fake_max)

    await process_update(tap(first, "task.btn_save"), fake_max)
    assert await tasks() == []
    assert fake_max.sent[-1]["text"] == t("task.draft_stale")
    assert ("task_draft_stale", {"action": "save"}) in await events()
    assert draft_fields(await state_data()) == {
        "title": "Заплатить налог",
        "due_date": "2026-11-05",
    }

    # «Сохранить» под B сохраняет именно B
    await process_update(tap(second, "task.btn_save"), fake_max)
    [task] = await tasks()
    assert (task.title, task.due_date) == ("Заплатить налог", date(2026, 11, 5))


async def test_edit_under_old_confirm_does_not_open_new_draft(fake_max, bot_name, miniapp_api):
    """«Изменить» под первым из двух confirm — форма 17 без чужого черновика (#96)."""
    first, second = await two_drafts(fake_max)

    old = await miniapp_api.request(
        "GET", "/api/me", USER_ID, start_param=payload_of(first, "task.btn_edit")
    )
    assert (old.json()["draft"], old.json()["draft_stale"]) == (None, True)

    new = await miniapp_api.request(
        "GET", "/api/me", USER_ID, start_param=payload_of(second, "task.btn_edit")
    )
    assert new.json()["draft"] == {**NO_TIME, "title": "Заплатить налог", "due_date": "2026-11-05"}
    assert new.json()["draft_stale"] is False


async def test_cancel_under_old_confirm_keeps_new_draft(fake_max, no_bot_name):
    first, second = await two_drafts(fake_max)

    await process_update(tap(first, "task.btn_cancel"), fake_max)
    assert fake_max.sent[-1]["text"] == t("task.cancelled")
    assert ("task_draft_stale", {"action": "cancel"}) in await events()
    assert draft_fields(await state_data())["title"] == "Заплатить налог"

    await process_update(tap(second, "task.btn_save"), fake_max)
    assert [x.title for x in await tasks()] == ["Заплатить налог"]


async def test_add_anyway_under_old_duplicate_does_not_save_new_draft(fake_max, no_bot_name):
    await onboarded()
    async with SessionLocal() as s:
        s.add(Task(user_id=USER_ID, title="Оплатить аренду", due_date=date(2026, 10, 30)))
        await s.commit()
    await process_update(write("оплатить аренду 30 октября"), fake_max)
    duplicate = fake_max.sent[-1]
    assert duplicate["text"] == t("task.duplicate", date="30 октября")
    await process_update(write("заплатить налог 5 ноября"), fake_max)

    await process_update(tap(duplicate, "task.btn_add_anyway"), fake_max)
    assert fake_max.sent[-1]["text"] == t("task.draft_stale")
    assert ("task_draft_stale", {"action": "anyway"}) in await events()
    assert [x.title for x in await tasks()] == ["Оплатить аренду"]  # только прежняя


async def test_yes_under_old_past_date_does_not_touch_new_draft(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5.09.2026"), fake_max)
    past = fake_max.sent[-1]
    await process_update(write("заплатить налог 5 ноября"), fake_max)

    await process_update(tap(past, "task.btn_yes"), fake_max)
    assert fake_max.sent[-1]["text"] == t("task.draft_stale")
    assert ("task_draft_stale", {"action": "yes"}) in await events()
    assert draft_fields(await state_data()) == {
        "title": "Заплатить налог",
        "due_date": "2026-11-05",
    }
    assert await tasks() == []


@pytest.mark.parametrize("action", ["save", "anyway", "yes", "cancel"])
async def test_legacy_button_without_id_does_not_save_new_draft(fake_max, no_bot_name, action):
    """Кнопка, отправленная до #87 (payload без id), с черновиком с id — «устарел»."""
    payload = {
        "save": task_chat.SAVE,
        "anyway": task_chat.ANYWAY,
        "yes": task_chat.YES,
        "cancel": task_chat.CANCEL,
    }[action]
    await onboarded()
    await process_update(write("заплатить налог 5 ноября"), fake_max)

    await process_update(press(payload), fake_max)

    assert await tasks() == []
    expected = "task.cancelled" if action == "cancel" else "task.draft_stale"
    assert fake_max.sent[-1]["text"] == t(expected)
    assert draft_fields(await state_data())["title"] == "Заплатить налог"
    assert ("task_draft_stale", {"action": action}) in await events()


@pytest.mark.parametrize(
    "bad",
    ["task:saveX", "task:save:", "task:anywayX", "task:anyway:", "task:yes:", "task:cancelX"],
)
async def test_malformed_payload_is_unknown(fake_max, no_bot_name, bad):
    """Payload не вида «<action>» / «<action>:<id>» — «не понял», черновик не тронут."""
    await onboarded()
    await process_update(write("заплатить налог 5 ноября"), fake_max)
    before = (await state_data())["task_draft"]

    await process_update(press(bad), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")
    assert await tasks() == []
    assert (await state_data())["task_draft"] == before
    assert all(name != "task_draft_stale" for name, _ in await events())


async def test_duplicate_found_on_save_keeps_draft_and_add_anyway_saves(fake_max, no_bot_name):
    """Confirm, затем такая же задача появилась (например, из мини-аппа): «Сохранить» → duplicate.

    Черновик тот же (id не меняется): «Добавить всё равно» сохраняет, кнопки confirm живы.
    """
    await onboarded()
    await process_update(write("оплатить аренду 5 ноября"), fake_max)
    confirm = fake_max.sent[-1]
    draft_id = (await state_data())["task_draft"]["id"]
    async with SessionLocal() as s:
        s.add(Task(user_id=USER_ID, title="Оплатить аренду", due_date=date(2026, 11, 5)))
        await s.commit()

    await process_update(tap(confirm, "task.btn_save"), fake_max)
    duplicate = fake_max.sent[-1]
    assert duplicate["text"] == t("task.duplicate", date="5 ноября")
    assert (await state_data())["task_draft"]["id"] == draft_id
    assert payload_of(duplicate, "task.btn_add_anyway") == f"{task_chat.ANYWAY}:{draft_id}"

    await process_update(tap(duplicate, "task.btn_add_anyway"), fake_max)
    assert fake_max.sent[-1]["text"] == t(
        "task.saved", count=2, count_word=t("calendar_ready.count_few")
    )
    assert len(await tasks()) == 2
    assert all(name != "task_draft_stale" for name, _ in await events())


async def test_legacy_button_with_legacy_draft_works_as_before(fake_max, no_bot_name):
    """Черновик и кнопка — оба до #87 (без id): «Сохранить» сохраняет, как раньше."""
    await onboarded()
    async with SessionLocal() as s:
        s.add(
            DialogState(
                user_id=USER_ID,
                state=None,
                data={"task_draft": {"title": "Оплатить аренду", "due_date": "2026-11-05"}},
            )
        )
        await s.commit()

    await process_update(press(task_chat.SAVE), fake_max)

    [task] = await tasks()
    assert (task.title, task.due_date) == ("Оплатить аренду", date(2026, 11, 5))
    assert fake_max.sent[-1]["text"] == t(
        "task.saved", count=1, count_word=t("calendar_ready.count_one")
    )


async def test_legacy_button_without_draft_is_unknown(fake_max, no_bot_name):
    await onboarded()
    for payload in (task_chat.SAVE, task_chat.ANYWAY, task_chat.YES):
        await process_update(press(payload), fake_max)
    assert await tasks() == []
    # третий непонятый подряд — с `unknown_3` («Написать нам» теперь всегда, D13)
    unknown_3 = f"{t('fallback.unknown')}\n{t('fallback.unknown_3')}"
    assert [m["text"] for m in fake_max.sent] == [t("fallback.unknown")] * 2 + [unknown_3]


async def test_api_me_reads_draft_with_id(fake_max, bot_name, miniapp_api):
    """`/api/me` отдаёт форме 17 последний черновик; лишнее поле `id` ему не мешает."""
    await two_drafts(fake_max)
    assert "id" in (await state_data())["task_draft"]

    body = (await miniapp_api.request("GET", "/api/me", USER_ID)).json()
    assert body["draft"] == {**NO_TIME, "title": "Заплатить налог", "due_date": "2026-11-05"}


# --- #87: confirm обещает напоминание, только если оно будет -------------------------------


@pytest.mark.parametrize(
    ("tz", "now", "text", "day", "remind"),
    [
        # Москва, 12:00: напоминание на завтра (сегодня в 10:00) уже прошло
        ("Europe/Moscow", NOW, "сегодня", "23 сентября", False),
        ("Europe/Moscow", NOW, "завтра", "24 сентября", False),
        ("Europe/Moscow", NOW, "послезавтра", "25 сентября", True),
        # Москва, 09:00: до 10:00 — на завтра ещё напомним
        ("Europe/Moscow", datetime(2026, 9, 23, 6, 0, tzinfo=UTC), "завтра", "24 сентября", True),
        # Владивосток, 19:00 (UTC+10)
        ("Asia/Vladivostok", NOW, "сегодня", "23 сентября", False),
        ("Asia/Vladivostok", NOW, "завтра", "24 сентября", False),
        ("Asia/Vladivostok", NOW, "послезавтра", "25 сентября", True),
    ],
)
async def test_confirm_promises_reminder_only_if_it_will_be_sent(
    fake_max, no_bot_name, clock, tz, now, text, day, remind
):
    clock["now"] = now
    await onboarded(timezone=tz)
    await process_update(write(f"оплатить аренду {text}"), fake_max)

    expected = (confirm_text if remind else no_remind_text)("Оплатить аренду", day)
    assert fake_max.sent[-1]["text"] == expected
    assert labels(fake_max.sent[-1]) == [[t("task.btn_save")], [t("task.btn_cancel")]]

    # то же решение, что при сохранении: уведомление создано ровно тогда, когда обещано
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    async with SessionLocal() as s:
        pending = (
            (await s.execute(select(Notification).where(Notification.status == "pending")))
            .scalars()
            .all()
        )
    assert len(pending) == (1 if remind else 0)


# --- #103: время в сообщении --------------------------------------------------------------------


def time_confirm_text(title: str, day: str, at: str) -> str:
    return t("task.confirm", title=title, date=day, remind_when=t("task.remind_same_day"), time=at)


async def task_notifications(task_id: int) -> list[tuple[str, datetime]]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification).where(
                Notification.item_type == "task", Notification.item_id == task_id
            )
        )
        return [(n.status, n.send_at.replace(tzinfo=UTC)) for n in rows]


async def test_time_in_message_reminds_on_due_day_at_that_time(fake_max, bot_name, miniapp_api):
    """«Оплатить аренду 5 ноября в 15:30» → в день срока в 15:30; «Изменить» несёт время."""
    await onboarded()
    await process_update(write("Оплатить аренду 5 ноября в 15:30"), fake_max)

    confirm = fake_max.sent[-1]
    assert confirm["text"] == time_confirm_text("Оплатить аренду", "5 ноября", "15:30")
    assert "в день срока в 15:30" in confirm["text"]
    assert draft_fields(await state_data()) == {
        "title": "Оплатить аренду",
        "due_date": "2026-11-05",
        "remind_hour": 15,
        "remind_minute": 30,
    }
    # «Изменить» → форма 17 получает и время
    edit = payload_of(confirm, "task.btn_edit")
    body = (await miniapp_api.request("GET", "/api/me", USER_ID, start_param=edit)).json()
    assert body["draft"] == {
        "title": "Оплатить аренду",
        "due_date": "2026-11-05",
        "remind_hour": 15,
        "remind_minute": 30,
    }

    await process_update(tap(confirm, "task.btn_save"), fake_max)
    [task] = await tasks()
    assert (task.remind_offset_days, task.remind_hour, task.remind_minute) == (0, 15, 30)
    # 05.11 15:30 по Москве = 12:30 UTC
    assert await task_notifications(task.id) == [
        ("pending", datetime(2026, 11, 5, 12, 30, tzinfo=UTC))
    ]
    assert ("task_created", {"source": "chat", "remind_offset": 0}) in await events()


async def test_time_in_message_uses_user_timezone(fake_max, no_bot_name):
    await onboarded(timezone="Asia/Vladivostok")
    await process_update(write("позвонить бухгалтеру 5 ноября в 9:05"), fake_max)
    assert fake_max.sent[-1]["text"] == time_confirm_text(
        "Позвонить бухгалтеру", "5 ноября", "9:05"
    )
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    [task] = await tasks()
    # 05.11 09:05 во Владивостоке (UTC+10) = 04.11 23:05 UTC
    assert await task_notifications(task.id) == [
        ("pending", datetime(2026, 11, 4, 23, 5, tzinfo=UTC))
    ]


@pytest.mark.parametrize(
    ("text", "remind"),
    [
        ("позвонить сегодня в 9:00", False),  # сейчас 12:00 по Москве — время прошло
        ("позвонить сегодня в 15:00", True),
    ],
)
async def test_time_today_promises_reminder_only_if_ahead(fake_max, no_bot_name, text, remind):
    await onboarded()
    await process_update(write(text), fake_max)
    at = "15:00" if remind else "9:00"
    expected = (
        time_confirm_text("Позвонить", "23 сентября", at)
        if remind
        else no_remind_text("Позвонить", "23 сентября")
    )
    assert fake_max.sent[-1]["text"] == expected
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    [task] = await tasks()
    assert bool(await task_notifications(task.id)) is remind


async def test_past_date_yes_keeps_time(fake_max, no_bot_name):
    await onboarded()
    await process_update(write("оплатить аренду 5.11.2025 в 18:45"), fake_max)
    await process_update(tap(fake_max.sent[-1], "task.btn_yes"), fake_max)
    assert fake_max.sent[-1]["text"] == time_confirm_text("Оплатить аренду", "5 ноября", "18:45")
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    [task] = await tasks()
    assert (task.due_date, task.remind_hour, task.remind_minute) == (date(2026, 11, 5), 18, 45)


async def test_time_without_date_keeps_time_for_buttons_and_form(fake_max, bot_name, miniapp_api):
    """«позвонить бухгалтеру в 15:30» — экран 10; «Через неделю» и форма 17 получают время."""
    await onboarded()
    await process_update(write("позвонить бухгалтеру в 15:30"), fake_max)
    no_date = fake_max.sent[-1]
    assert no_date["text"] == t("fallback.no_date", title="Позвонить бухгалтеру")

    pick = payload_of(no_date, "fallback.btn_pick")
    body = (await miniapp_api.request("GET", "/api/me", USER_ID, start_param=pick)).json()
    assert (body["draft"]["remind_hour"], body["draft"]["remind_minute"]) == (15, 30)

    await process_update(tap(no_date, "fallback.btn_week"), fake_max)
    assert fake_max.sent[-1]["text"] == time_confirm_text(
        "Позвонить бухгалтеру", "30 сентября", "15:30"
    )
    await process_update(tap(fake_max.sent[-1], "task.btn_save"), fake_max)
    [task] = await tasks()
    assert (task.due_date, task.remind_offset_days, task.remind_hour, task.remind_minute) == (
        date(2026, 9, 30),
        0,
        15,
        30,
    )


def test_draft_with_time_and_old_draft_without_it():
    """Время в черновике — оба ключа вместе; старый черновик без них читается как «без времени»."""
    assert fallback.draft("Аренда", date(2026, 11, 5), "ab12cd34", time(9, 5)) == {
        "title": "Аренда",
        "due_date": "2026-11-05",
        "id": "ab12cd34",
        "remind_hour": 9,
        "remind_minute": 5,
    }
    assert task_chat.draft_time({"title": "Аренда", "due_date": "2026-11-05"}) is None
    assert task_chat.draft_time({"remind_hour": 24, "remind_minute": 0}) is None
    assert task_chat.draft_time({"remind_hour": 7, "remind_minute": 45}) == time(7, 45)
    assert task_chat.format_time(time(9, 5)) == "9:05"
    assert task_chat.format_time(time(10, 0)) == "10:00"
