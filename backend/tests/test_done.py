"""Экран 8: «Отметить выполненным» и «Отменить отметку» из бота (T7).

Справочник — фикстура backend/tests/fixtures/obligations.yaml (ТЕСТОВЫЕ ДАННЫЕ).
Апдейты — фикстура callback_start_check.json с подменённым payload.
"""

import asyncio
import copy
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.bot.dispatcher import process_update
from app.bot.handlers import common
from app.calendar import marks
from app.calendar.reminders import tick
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Event, Notification, Profile, Task, User, UserObligation
from tests.conftest import FakeMax, load_update

pytestmark = pytest.mark.usefixtures("fixture_reference")

USER = 42  # совпадает с user_id в фикстуре callback-апдейта
MSK = "Europe/Moscow"
DUE = date(2026, 10, 28)
YEARLY = "Тестовое годовое обязательство"
QUARTERLY = "Тестовое квартальное обязательство"
BOT = "pareto_calendar_bot"
DISCLAIMER = (
    "Вы отметили обязательство выполненным. "
    "Продукт не проверяет факт оплаты или отправки отчётности."
)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


NOW = utc(2026, 10, 21, 8)  # 11:00 МСК, в день d7 для срока 28 октября


# --- помощники (ими пользуется и test_howto.py) ---------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(common, "now", lambda: state["now"])
    return state


@pytest.fixture
def bot_username(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT)


@pytest.fixture
def no_bot_username(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")


def callback(payload: str, callback_id: str = "cb-1", *, mid: str | None = "mid-1") -> dict:
    update = copy.deepcopy(load_update("callback_start_check"))
    update["callback"]["callback_id"] = callback_id
    update["callback"]["payload"] = payload
    if mid is None:
        update["message"]["body"].pop("mid")
    else:
        update["message"]["body"]["mid"] = mid
    return update


async def add_user(session, user_id: int = USER) -> None:
    if await session.get(User, user_id) is None:
        session.add(User(user_id=user_id))
        await session.flush()
        session.add(Profile(user_id=user_id, timezone=MSK))
        await session.flush()


async def add_uo(
    *, ob: str = "test_yearly", due: date = DUE, user_id: int = USER, done_at=None
) -> int:
    async with SessionLocal() as session:
        await add_user(session, user_id)
        uo = UserObligation(
            user_id=user_id,
            obligation_id=ob,
            rule_version=1,
            original_date=due,
            due_date=due,
            done_at=done_at,
        )
        session.add(uo)
        await session.commit()
        return uo.id


async def add_task(title: str, due: date, user_id: int = USER) -> int:
    async with SessionLocal() as session:
        await add_user(session, user_id)
        task = Task(user_id=user_id, title=title, due_date=due)
        session.add(task)
        await session.commit()
        return task.id


async def add_notification(
    item_id: int, kind: str, send_at: datetime, status: str = "pending", item_type="obligation"
) -> None:
    async with SessionLocal() as session:
        session.add(
            Notification(
                user_id=USER,
                item_type=item_type,
                item_id=item_id,
                kind=kind,
                send_at=send_at,
                status=status,
                attempts=1 if status == "sent" else 0,
            )
        )
        await session.commit()


async def notifications(item_id: int, item_type: str = "obligation") -> dict[str, list[str]]:
    """kind → статусы по порядку создания."""
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Notification)
            .where(Notification.item_type == item_type, Notification.item_id == item_id)
            .order_by(Notification.id)
        )
        result: dict[str, list[str]] = {}
        for n in rows:
            result.setdefault(n.kind, []).append(n.status)
        return result


async def events(name: str) -> list[dict]:
    async with SessionLocal() as session:
        rows = await session.scalars(select(Event).where(Event.name == name).order_by(Event.id))
        return [e.props for e in rows]


async def done_at(uo_id: int):
    async with SessionLocal() as session:
        return (await session.get(UserObligation, uo_id)).done_at


def payloads(attachments) -> list[list[str]]:
    """Клавиатура → ряды: callback — payload, open_app — `open:<payload>`, link — `link:<url>`."""
    if not attachments:
        return []
    rows = attachments[0]["payload"]["buttons"]

    def one(b: dict) -> str:
        if b["type"] == "callback":
            return b["payload"]
        if b["type"] == "link":
            return f"link:{b['url']}"
        return f"open:{b.get('payload')}"

    return [[one(b) for b in row] for row in rows]


def texts(attachments) -> list[list[str]]:
    return [[b["text"] for b in row] for row in attachments[0]["payload"]["buttons"]]


def sent_texts(fake: FakeMax) -> list[str]:
    return [m["text"] for m in fake.sent]


# --- отметка --------------------------------------------------------------------------------


async def test_done_from_reminder(fake_max, clock, bot_username):
    uo_id = await add_uo()
    next_id = await add_uo(ob="test_quarterly", due=date(2026, 11, 25))
    await add_notification(uo_id, "d7", utc(2026, 10, 21, 7), status="sent")

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == [
        f"Отметил: {YEARLY} — выполнено.\n{DISCLAIMER}\n"
        "Следующее — 25 ноября: тестовое квартальное обязательство."
    ]
    attachments = fake_max.sent[0]["attachments"]
    assert payloads(attachments) == [["open:None", f"d:undo:obligation:{uo_id}"]]
    assert texts(attachments) == [["Открыть календарь", "Отменить отметку"]]
    assert await done_at(uo_id) is not None
    assert await done_at(next_id) is None
    assert await events("reminder_clicked") == [{"kind": "d7", "action": "done"}]
    assert await events("item_done") == [
        {
            "item_id": uo_id,
            "item_type": "obligation",
            "source": "reminder",
            "days_before_deadline": 7,
        }
    ]


async def test_done_from_howto_has_howto_source_and_no_reminder_click(fake_max, clock):
    uo_id = await add_uo()

    await process_update(callback(f"h:done:obligation:{uo_id}"), fake_max)

    assert await done_at(uo_id) is not None
    assert await events("reminder_clicked") == []
    assert [e["source"] for e in await events("item_done")] == ["howto"]


async def test_reminder_kind_unknown_without_sent_notification(fake_max, clock):
    uo_id = await add_uo()

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert await events("reminder_clicked") == [{"kind": "unknown", "action": "done"}]


async def test_second_press_says_already_and_marks_once(fake_max, clock):
    uo_id = await add_uo()

    await process_update(callback(f"r:done:obligation:{uo_id}", "cb-1"), fake_max)
    first = await done_at(uo_id)
    await process_update(callback(f"r:done:obligation:{uo_id}", "cb-2"), fake_max)

    assert await done_at(uo_id) == first
    assert sent_texts(fake_max)[1] == "Это событие уже отмечено 21 октября."
    assert len(await events("item_done")) == 1


async def test_parallel_presses_mark_once(clock):
    uo_id = await add_uo()
    fakes = [FakeMax(), FakeMax()]

    await asyncio.gather(
        *(
            process_update(callback(f"r:done:obligation:{uo_id}", f"cb-{i}"), fake)
            for i, fake in enumerate(fakes)
        )
    )

    assert len(await events("item_done")) == 1
    replies = sorted(fake.sent[0]["text"] for fake in fakes)
    assert replies[1] == "Это событие уже отмечено 21 октября."
    assert replies[0].startswith("Отметил:")


async def test_mark_overdue_gives_negative_days(fake_max, clock):
    """D19: просроченное отметить можно, days_before_deadline отрицательный."""
    uo_id = await add_uo(due=date(2026, 10, 19))

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert await done_at(uo_id) is not None
    assert (await events("item_done"))[0]["days_before_deadline"] == -2


async def test_last_in_year(fake_max, clock):
    uo_id = await add_uo()
    await add_uo(ob="test_quarterly", due=date(2027, 1, 25))

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == [
        f"Отметил: {YEARLY} — выполнено.\n{DISCLAIMER}\n"
        "Это было последнее обязательное событие в 2026 году. "
        "Следующее — 25 января 2027: тестовое квартальное обязательство."
    ]


async def test_nothing_next(fake_max, clock):
    uo_id = await add_uo()
    await add_uo(ob="test_quarterly", due=date(2026, 10, 1))  # просроченное — не «следующее»

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == [
        f"Отметил: {YEARLY} — выполнено.\n{DISCLAIMER}\nБольше сроков в календаре нет."
    ]


async def test_next_can_be_own_task(fake_max, clock):
    uo_id = await add_uo()
    await add_uo(ob="test_quarterly", due=date(2026, 11, 25))
    await add_task("Оплатить аренду", date(2026, 11, 5))

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max)[0].endswith("Следующее — 5 ноября: оплатить аренду.")


async def test_done_task_from_reminder(fake_max, clock):
    task_id = await add_task("Оплатить аренду", date(2026, 10, 24))
    await add_notification(task_id, "task", utc(2026, 10, 21, 7), "sent", item_type="task")

    await process_update(callback(f"r:done:task:{task_id}"), fake_max)

    assert sent_texts(fake_max)[0].startswith("Отметил: Оплатить аренду — выполнено.")
    assert await events("reminder_clicked") == [{"kind": "task", "action": "done"}]
    assert (await events("item_done"))[0]["item_type"] == "task"


async def test_open_button_hidden_without_bot_username(fake_max, clock, no_bot_username):
    uo_id = await add_uo()

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert payloads(fake_max.sent[0]["attachments"]) == [[f"d:undo:obligation:{uo_id}"]]


async def test_foreign_item_is_ignored(fake_max, clock):
    uo_id = await add_uo(user_id=777)

    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert await done_at(uo_id) is None
    assert fake_max.sent == []


async def test_mark_error_shows_retry_and_keeps_status(fake_max, clock, monkeypatch):
    uo_id = await add_uo()

    async def broken(*args, **kwargs):
        raise OperationalError("UPDATE", {}, Exception("database is locked"))

    monkeypatch.setattr(marks, "mark_done", broken)
    await process_update(callback(f"r:done:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == ["Не получилось. Проверьте интернет и нажмите «Повторить»."]
    assert payloads(fake_max.sent[0]["attachments"]) == [[f"r:done:obligation:{uo_id}"]]
    assert await done_at(uo_id) is None
    assert await events("error") == [{"where": "item_done", "kind": "db"}]


# --- уведомления: правило 1 ------------------------------------------------------------------


async def test_after_mark_no_reminder_arrives_and_undo_returns_them(fake_max, clock):
    """Событие с d1 и overdue: отметка отменяет оба, отмена отметки возвращает."""
    uo_id = await add_uo()
    await add_notification(uo_id, "d7", utc(2026, 10, 21, 7), status="sent")
    await add_notification(uo_id, "d1", utc(2026, 10, 27, 7))
    await add_notification(uo_id, "overdue", utc(2026, 10, 29, 7))

    await process_update(callback(f"r:done:obligation:{uo_id}", "cb-done"), fake_max)

    assert await notifications(uo_id) == {
        "d7": ["sent"],
        "d1": ["cancelled"],
        "overdue": ["cancelled"],
    }
    await tick(utc(2026, 10, 27, 7), fake_max)
    await tick(utc(2026, 10, 29, 7), fake_max)
    assert len(fake_max.sent) == 1  # только ответ экрана 8, напоминаний нет

    await process_update(callback(f"d:undo:obligation:{uo_id}", "cb-undo"), fake_max)

    # d7 уже ушло и заново не создаётся; d30 — в прошлом; d1 и overdue снова ждут отправки.
    assert await notifications(uo_id) == {
        "d7": ["sent"],
        "d1": ["cancelled", "pending"],
        "overdue": ["cancelled", "pending"],
    }
    await tick(utc(2026, 10, 27, 7), fake_max)
    assert sent_texts(fake_max)[-1] == f"Завтра, 28 октября — {YEARLY}. Это последний день срока."


# --- «Отменить отметку» ----------------------------------------------------------------------


async def test_undo_edits_message_and_unmarks(fake_max, clock):
    uo_id = await add_uo(done_at=NOW)

    await process_update(callback(f"d:undo:obligation:{uo_id}", mid="mid-8"), fake_max)

    undone = "Отменил отметку. Напомню, как планировал."
    assert fake_max.edited == [{"message_id": "mid-8", "text": undone, "attachments": []}]
    assert fake_max.sent == []
    assert await done_at(uo_id) is None
    assert await events("item_undone") == [
        {"item_id": uo_id, "item_type": "obligation", "source": "bot"}
    ]


async def test_second_undo_writes_nothing(fake_max, clock):
    uo_id = await add_uo(done_at=NOW)

    await process_update(callback(f"d:undo:obligation:{uo_id}", "cb-1"), fake_max)
    await process_update(callback(f"d:undo:obligation:{uo_id}", "cb-2"), fake_max)

    assert len(await events("item_undone")) == 1
    assert len(fake_max.edited) == 2


async def test_undo_without_mid_replies(fake_max, clock):
    uo_id = await add_uo(done_at=NOW)

    await process_update(callback(f"d:undo:obligation:{uo_id}", mid=None), fake_max)

    assert fake_max.edited == []
    assert sent_texts(fake_max) == ["Отменил отметку. Напомню, как планировал."]
    assert await done_at(uo_id) is None


async def test_undo_task_returns_task_notification(fake_max, clock):
    task_id = await add_task("Оплатить аренду", date(2026, 10, 30))
    await process_update(callback(f"r:done:task:{task_id}", "cb-1"), fake_max)

    await process_update(callback(f"d:undo:task:{task_id}", "cb-2"), fake_max)

    assert await notifications(task_id, "task") == {"task": ["pending"]}
    assert [e["item_type"] for e in await events("item_undone")] == ["task"]


async def test_undo_edit_happens_outside_write_transaction(clock):
    """Правка сообщения — сетевой вызов — не держит блокировку записи SQLite."""
    uo_id = await add_uo(done_at=NOW)

    class ProbeEditMax(FakeMax):
        async def edit_message(self, message_id, text, **kwargs):
            async with SessionLocal() as probe:
                probe.add(Event(user_id=None, name="probe", props={}))
                await asyncio.wait_for(probe.commit(), timeout=2)
            return await super().edit_message(message_id, text, **kwargs)

    max_client = ProbeEditMax()
    await process_update(callback(f"d:undo:obligation:{uo_id}"), max_client)

    assert len(max_client.edited) == 1
    assert await done_at(uo_id) is None
