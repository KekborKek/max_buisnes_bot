"""T6 (#50): отправка напоминаний (reminders.md, «Отправка») и кнопка «Напомнить завтра».

Справочник — фикстура backend/tests/fixtures/obligations.yaml (ТЕСТОВЫЕ ДАННЫЕ):
`test_yearly` — needs_prep: true, `test_quarterly` — needs_prep: false. Даты и пояса —
тестовые; ожидаемые моменты отправки записаны явно, а не вычислены той же формулой, что в коде.
"""

import asyncio
import copy
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.bot.dispatcher import process_update
from app.bot.handlers import common
from app.calendar import loader
from app.calendar.reminders import (
    cancel_pending,
    resync_user_notifications,
    sync_obligation_notifications,
    tick,
)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Event, Notification, Profile, Task, User, UserObligation
from tests.conftest import FakeMax, load_update

pytestmark = pytest.mark.usefixtures("fixture_reference")

MSK = "Europe/Moscow"
USER = 42  # совпадает с user_id в фикстуре callback-апдейта
DUE = date(2026, 10, 28)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
YEARLY = "Тестовое годовое обязательство"
QUARTERLY = "Тестовое квартальное обязательство"
BOT = "pareto_calendar_bot"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# 10:00 МСК = 07:00 UTC
D7_AT = utc(2026, 10, 21, 7)


@pytest.fixture
def bot_username(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT)


# --- помощники --------------------------------------------------------------------------------


async def _user(session, user_id=USER, tz=MSK) -> None:
    if await session.get(User, user_id) is None:
        session.add(User(user_id=user_id))
        await session.flush()
        session.add(Profile(user_id=user_id, timezone=tz))
        await session.flush()


async def _uo(
    session, *, ob="test_yearly", due=DUE, original=None, user_id=USER, done=False
) -> UserObligation:
    await _user(session, user_id)
    uo = UserObligation(
        user_id=user_id,
        obligation_id=ob,
        rule_version=1,
        original_date=original or due,
        due_date=due,
        done_at=LONG_AGO if done else None,
    )
    session.add(uo)
    await session.flush()
    return uo


async def _notify(session, item_type, item_id, kind, send_at, user_id=USER) -> Notification:
    n = Notification(
        user_id=user_id,
        item_type=item_type,
        item_id=item_id,
        kind=kind,
        send_at=send_at,
        status="pending",
        attempts=0,
    )
    session.add(n)
    await session.flush()
    return n


async def _one_d7(**uo_kwargs) -> tuple[int, int]:
    """Обязательство и его d7 на 10:00 МСК 21 октября. → (uo.id, notification.id)."""
    async with SessionLocal() as session:
        uo = await _uo(session, **uo_kwargs)
        n = await _notify(session, "obligation", uo.id, "d7", D7_AT)
        await session.commit()
        return uo.id, n.id


async def _get(nid: int) -> Notification:
    async with SessionLocal() as session:
        return await session.get(Notification, nid)


async def _events(name: str) -> list[dict]:
    async with SessionLocal() as session:
        rows = await session.scalars(select(Event).where(Event.name == name).order_by(Event.id))
        return [e.props for e in rows]


def _payloads(attachments) -> list[list[str]]:
    """Клавиатура → ряды payload (у open_app — `open:<payload>`)."""
    if not attachments:
        return []
    rows = attachments[0]["payload"]["buttons"]
    return [
        [b["payload"] if b["type"] == "callback" else f"open:{b.get('payload')}" for b in row]
        for row in rows
    ]


def _texts(attachments) -> list[list[str]]:
    return [[b["text"] for b in row] for row in attachments[0]["payload"]["buttons"]]


class FailingMax(FakeMax):
    """MAX не принимает сообщение первые `fails` раз."""

    def __init__(self, fails: int = 10**6) -> None:
        super().__init__()
        self.fails = fails
        self.calls = 0

    async def send_message(self, text, **kwargs):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("MAX недоступен")
        return await super().send_message(text, **kwargs)


class SlowMax(FakeMax):
    async def send_message(self, text, **kwargs):
        await asyncio.sleep(0.2)
        return await super().send_message(text, **kwargs)


# --- одиночные сообщения: тексты и кнопки ------------------------------------------------------


async def test_d7_text_buttons_status_and_event(fake_max):
    uo_id, nid = await _one_d7()

    await tick(D7_AT - timedelta(minutes=1), fake_max)
    assert fake_max.sent == []

    now = D7_AT + timedelta(seconds=30)
    await tick(now, fake_max)

    assert len(fake_max.sent) == 1
    msg = fake_max.sent[0]
    assert msg["user_id"] == USER
    assert msg["text"] == (
        f"Через 7 дней, 28 октября — {YEARLY}. Если опоздать: Тестовый текст о пене."
    )
    assert _payloads(msg["attachments"]) == [
        [f"r:done:obligation:{uo_id}"],
        [f"r:howto:obligation:{uo_id}", f"r:snooze:obligation:{uo_id}"],
    ]
    assert _texts(msg["attachments"]) == [
        ["Отметить выполненным"],
        ["Как сделать", "Напомнить завтра"],
    ]
    n = await _get(nid)
    assert (n.status, n.attempts) == ("sent", 1)
    assert n.send_at.replace(tzinfo=UTC) == now  # аренда снята: фактическое время отправки
    assert await _events("reminder_sent") == [
        {"kind": "d7", "item_id": uo_id, "item_type": "obligation", "grouped": False}
    ]


@pytest.mark.parametrize(
    ("kind", "at", "text", "rows"),
    [
        (
            "d30",
            utc(2026, 9, 28, 7),
            f"Через месяц, 28 октября — {YEARLY}. "
            "К этому сроку нужно подготовиться — предупреждаю заранее.",
            [["r:howto:obligation:{id}", "open:item_obligation_{id}"]],
        ),
        (
            "d1",
            utc(2026, 10, 27, 7),
            f"Завтра, 28 октября — {YEARLY}. Это последний день срока.",
            [["r:done:obligation:{id}"], ["r:howto:obligation:{id}"]],
        ),
        (
            "overdue",
            utc(2026, 10, 29, 7),
            f"Срок 28 октября прошёл, отметки нет: {YEARLY}. Если вы уже всё сделали — "
            "отметьте, и я перестану считать его просроченным.",
            [["r:done:obligation:{id}"], ["r:howto:obligation:{id}"]],
        ),
        (
            "snooze",
            utc(2026, 10, 22, 7),
            f"Напоминаю: 28 октября — {YEARLY}.",
            [["r:done:obligation:{id}"], ["r:howto:obligation:{id}"]],
        ),
    ],
)
async def test_each_kind_text_and_buttons(fake_max, bot_username, kind, at, text, rows):
    async with SessionLocal() as session:
        uo = await _uo(session)
        await _notify(session, "obligation", uo.id, kind, at)
        await session.commit()

    await tick(at, fake_max)

    assert [m["text"] for m in fake_max.sent] == [text]
    expected = [[p.format(id=uo.id) for p in row] for row in rows]
    assert _payloads(fake_max.sent[0]["attachments"]) == expected


async def test_task_text_and_buttons(fake_max, bot_username):
    async with SessionLocal() as session:
        await _user(session)
        task = Task(user_id=USER, title="Оплатить аренду", due_date=date(2026, 10, 24))
        session.add(task)
        await session.flush()
        await _notify(session, "task", task.id, "task", D7_AT)
        await session.commit()

    await tick(D7_AT, fake_max)

    assert [m["text"] for m in fake_max.sent] == ["Через 3 дня, 24 октября — Оплатить аренду."]
    assert _payloads(fake_max.sent[0]["attachments"]) == [
        [f"r:done:task:{task.id}"],
        [f"open:item_task_{task.id}"],
    ]
    # D31: у задачи и обязательства id могут совпасть — в событии есть item_type.
    assert await _events("reminder_sent") == [
        {"kind": "task", "item_id": task.id, "item_type": "task", "grouped": False}
    ]


async def test_shift_note_in_single_message(fake_max):
    async with SessionLocal() as session:
        uo = await _uo(session, due=date(2026, 10, 26), original=date(2026, 10, 25))
        await _notify(session, "obligation", uo.id, "d1", utc(2026, 10, 25, 7))
        await session.commit()

    await tick(utc(2026, 10, 25, 7), fake_max)

    assert fake_max.sent[0]["text"].startswith(
        "Завтра, 26 октября (перенос с 25 октября, выходной) — "
    )


async def test_open_button_hidden_without_bot_username(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")
    async with SessionLocal() as session:
        uo = await _uo(session)
        await _notify(session, "obligation", uo.id, "d30", utc(2026, 9, 28, 7))
        await session.commit()

    await tick(utc(2026, 9, 28, 7), fake_max)

    assert _payloads(fake_max.sent[0]["attachments"]) == [[f"r:howto:obligation:{uo.id}"]]


# --- правило 1: отмеченное и удалённое не уходит ----------------------------------------------


async def test_marked_before_send_is_cancelled(fake_max):
    _, nid = await _one_d7(done=True)

    await tick(D7_AT, fake_max)

    assert fake_max.sent == []
    assert (await _get(nid)).status == "cancelled"
    assert await _events("reminder_sent") == []


async def test_deleted_task_and_missing_obligation_are_cancelled(fake_max):
    async with SessionLocal() as session:
        await _user(session)
        task = Task(user_id=USER, title="Удалённая", due_date=DUE, deleted_at=LONG_AGO)
        session.add(task)
        await session.flush()
        n_task = await _notify(session, "task", task.id, "task", D7_AT)
        n_gone = await _notify(session, "obligation", 999_999, "d7", D7_AT)
        await session.commit()

    await tick(D7_AT, fake_max)

    assert fake_max.sent == []
    assert (await _get(n_task.id)).status == "cancelled"
    assert (await _get(n_gone.id)).status == "cancelled"


async def test_after_mark_no_further_reminder_arrives(fake_max):
    """Сквозной путь T4 → T6: отметка после d7 — d1 и overdue уже не приходят."""
    async with SessionLocal() as session:
        uo = await _uo(session)
        await sync_obligation_notifications(
            session, uo, needs_prep=True, tz=MSK, settings=None, now=LONG_AGO
        )
        await session.commit()

    await tick(D7_AT, fake_max)
    assert len(fake_max.sent) == 1

    async with SessionLocal() as session:
        uo = await session.get(UserObligation, uo.id)
        uo.done_at = D7_AT
        await sync_obligation_notifications(
            session, uo, needs_prep=True, tz=MSK, settings=None, now=D7_AT
        )
        await session.commit()

    for day in range(22, 31):
        await tick(utc(2026, 10, day, 12), fake_max)
    assert len(fake_max.sent) == 1


async def test_stale_reminders_are_not_sent_late(fake_max):
    """Планировщик стоял: «Через месяц» и «Через 7 дней» за день до срока уже не шлём."""
    async with SessionLocal() as session:
        uo = await _uo(session)
        await sync_obligation_notifications(
            session, uo, needs_prep=True, tz=MSK, settings=None, now=LONG_AGO
        )
        await session.commit()

    await tick(utc(2026, 10, 27, 7), fake_max)

    assert [m["text"] for m in fake_max.sent] == [
        f"Завтра, 28 октября — {YEARLY}. Это последний день срока."
    ]
    async with SessionLocal() as session:
        rows = await session.scalars(select(Notification).where(Notification.item_id == uo.id))
        statuses = {n.kind: n.status for n in rows}
    assert statuses == {"d30": "cancelled", "d7": "cancelled", "d1": "sent", "overdue": "pending"}


# --- d30 только для needs_prep ------------------------------------------------------------------


async def test_d30_only_for_needs_prep(fake_max):
    due = date(2026, 11, 27)
    async with SessionLocal() as session:
        for ob, needs_prep in (("test_yearly", True), ("test_quarterly", False)):
            uo = await _uo(session, ob=ob, due=due)
            await sync_obligation_notifications(
                session, uo, needs_prep=needs_prep, tz=MSK, settings=None, now=LONG_AGO
            )
        await session.commit()

    await tick(utc(2026, 10, 28, 7), fake_max)  # за 30 дней, 10:00 МСК

    assert [m["text"] for m in fake_max.sent] == [
        f"Через месяц, 27 ноября — {YEARLY}. "
        "К этому сроку нужно подготовиться — предупреждаю заранее."
    ]


# --- правило 2: группировка ---------------------------------------------------------------------


async def test_two_obligations_same_date_give_one_message(fake_max, bot_username):
    async with SessionLocal() as session:
        a = await _uo(session, ob="test_yearly")
        b = await _uo(session, ob="test_quarterly")
        na = await _notify(session, "obligation", a.id, "d7", D7_AT)
        nb = await _notify(session, "obligation", b.id, "d7", D7_AT)
        # другой пользователь, та же дата — своё сообщение
        c = await _uo(session, user_id=77)
        await _notify(session, "obligation", c.id, "d7", D7_AT, user_id=77)
        await session.commit()

    await tick(D7_AT, fake_max)

    mine = [m for m in fake_max.sent if m["user_id"] == USER]
    assert len(mine) == 1
    assert mine[0]["text"] == (
        f"Через 7 дней, 28 октября — два срока: {YEARLY} и {QUARTERLY}. "
        "Откройте календарь — там подробности и отметки."
    )
    assert _payloads(mine[0]["attachments"]) == [["open:None"]]  # без параметра запуска
    assert [m["user_id"] for m in fake_max.sent].count(77) == 1
    assert {(await _get(na.id)).status, (await _get(nb.id)).status} == {"sent"}
    grouped = [e for e in await _events("reminder_sent") if e["grouped"]]
    assert sorted(e["item_id"] for e in grouped) == sorted([a.id, b.id])


async def test_grouped_without_bot_username_has_no_buttons(fake_max, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")
    async with SessionLocal() as session:
        for ob in ("test_yearly", "test_quarterly"):
            uo = await _uo(session, ob=ob)
            await _notify(session, "obligation", uo.id, "d1", utc(2026, 10, 27, 7))
        await session.commit()

    await tick(utc(2026, 10, 27, 7), fake_max)

    assert len(fake_max.sent) == 1
    assert fake_max.sent[0]["text"].startswith("Завтра, 28 октября — два срока: ")
    assert fake_max.sent[0]["attachments"] is None


async def test_grouped_shift_note_when_all_moved_from_same_date(fake_max):
    async with SessionLocal() as session:
        for ob in ("test_yearly", "test_quarterly"):
            uo = await _uo(session, ob=ob, due=date(2026, 10, 26), original=date(2026, 10, 25))
            await _notify(session, "obligation", uo.id, "d1", utc(2026, 10, 25, 7))
        await session.commit()

    await tick(utc(2026, 10, 25, 7), fake_max)

    assert fake_max.sent[0]["text"].startswith(
        "Завтра, 26 октября (перенос с 25 октября, выходной) — два срока: "
    )


async def test_overdue_is_not_grouped_and_sent_once(fake_max):
    """D30: у `grouped` нет текста «срок прошёл» — overdue уходят поштучно; и только раз."""
    async with SessionLocal() as session:
        for ob in ("test_yearly", "test_quarterly"):
            uo = await _uo(session, ob=ob)
            await sync_obligation_notifications(
                session, uo, needs_prep=False, tz=MSK, settings=None, now=LONG_AGO
            )
        await session.commit()

    overdue_at = utc(2026, 10, 29, 7)  # на следующий день после срока, 10:00 МСК (D10)
    await tick(overdue_at, fake_max)
    await tick(overdue_at + timedelta(hours=2), fake_max)
    await tick(overdue_at + timedelta(days=3), fake_max)

    overdue = [m for m in fake_max.sent if m["text"].startswith("Срок 28 октября прошёл")]
    assert len(overdue) == 2
    assert {m["text"].rsplit(": ", 1)[1].split(".")[0] for m in overdue} == {YEARLY, QUARTERLY}


# --- ошибки отправки: повтор через час, потом failed ------------------------------------------


async def test_send_error_retries_in_an_hour_then_failed():
    _, nid = await _one_d7()
    max_client = FailingMax()

    await tick(D7_AT, max_client)
    n = await _get(nid)
    assert (n.status, n.attempts) == ("pending", 1)
    assert n.send_at.replace(tzinfo=UTC) == D7_AT + timedelta(hours=1)

    await tick(D7_AT + timedelta(minutes=30), max_client)
    assert max_client.calls == 1  # раньше часа повтора нет

    await tick(D7_AT + timedelta(hours=1), max_client)
    n = await _get(nid)
    assert (n.status, n.attempts) == ("failed", 2)

    await tick(D7_AT + timedelta(hours=5), max_client)
    assert max_client.calls == 2  # третьей попытки нет
    assert len(await _events("error")) == 2
    assert await _events("reminder_sent") == []


async def test_retry_succeeds():
    _, nid = await _one_d7()
    max_client = FailingMax(fails=1)

    await tick(D7_AT, max_client)
    await tick(D7_AT + timedelta(hours=1), max_client)

    n = await _get(nid)
    assert (n.status, n.attempts) == ("sent", 2)
    assert len(max_client.sent) == 1


# --- дубли и транзакции -----------------------------------------------------------------------


async def test_repeated_tick_does_not_resend(fake_max):
    await _one_d7()

    for minute in range(5):
        await tick(D7_AT + timedelta(minutes=minute), fake_max)

    assert len(fake_max.sent) == 1


async def test_parallel_ticks_send_once():
    await _one_d7()
    max_client = SlowMax()

    await asyncio.gather(tick(D7_AT, max_client), tick(D7_AT, max_client))

    assert len(max_client.sent) == 1
    assert len(await _events("reminder_sent")) == 1


class ProbeMax(FakeMax):
    """Во время отправки пишет в базу отдельной сессией.

    Если бы тик держал транзакцию записи, эта запись ждала бы busy_timeout (30 с) — здесь
    она обязана пройти за 2 с. Заодно видно, что «забрать» уже закоммичено.
    """

    def __init__(self) -> None:
        super().__init__()
        self.attempts_seen: list[int] = []

    async def send_message(self, text, **kwargs):
        async with SessionLocal() as probe:
            self.attempts_seen.append(await probe.scalar(select(func.max(Notification.attempts))))
            probe.add(Event(user_id=None, name="probe", props={}))
            await asyncio.wait_for(probe.commit(), timeout=2)
        return await super().send_message(text, **kwargs)


async def test_transaction_is_not_held_during_send():
    await _one_d7()
    max_client = ProbeMax()

    await tick(D7_AT, max_client)

    assert len(max_client.sent) == 1
    assert max_client.attempts_seen == [1]
    assert len(await _events("probe")) == 1


# --- часовые пояса -------------------------------------------------------------------------------


async def test_ten_am_in_three_timezones(fake_max):
    zones = {1: "Asia/Vladivostok", 2: "Europe/Moscow", 3: "Europe/Kaliningrad"}
    async with SessionLocal() as session:
        for user_id, tz in zones.items():
            await _user(session, user_id, tz)
            uo = await _uo(session, user_id=user_id)
            await sync_obligation_notifications(
                session, uo, needs_prep=False, tz=tz, settings=None, now=LONG_AGO
            )
        await session.commit()

    def recipients():
        return [m["user_id"] for m in fake_max.sent]

    await tick(utc(2026, 10, 20, 23, 59), fake_max)
    assert recipients() == []
    await tick(utc(2026, 10, 21, 0), fake_max)  # 10:00 во Владивостоке (UTC+10)
    assert recipients() == [1]
    await tick(utc(2026, 10, 21, 6, 59), fake_max)
    assert recipients() == [1]
    await tick(utc(2026, 10, 21, 7), fake_max)  # 10:00 в Москве (UTC+3)
    assert recipients() == [1, 2]
    await tick(utc(2026, 10, 21, 8), fake_max)  # 10:00 в Калининграде (UTC+2)
    assert recipients() == [1, 2, 3]


# --- «Напомнить завтра» -------------------------------------------------------------------------


def _snooze_update(uo_id: int, callback_id: str = "cb-snooze-1", *, mid: str | None = "mid-1"):
    update = copy.deepcopy(load_update("callback_start_check"))
    update["callback"]["callback_id"] = callback_id
    update["callback"]["payload"] = f"r:snooze:obligation:{uo_id}"
    if mid is None:
        update["message"]["body"].pop("mid")
    else:
        update["message"]["body"]["mid"] = mid
    return update


@pytest.fixture
def clock(monkeypatch):
    state = {"now": utc(2026, 10, 21, 8)}  # 11:00 МСК, в день d7
    monkeypatch.setattr(common, "now", lambda: state["now"])
    return state


async def _snoozes(uo_id: int) -> list[Notification]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Notification).where(Notification.item_id == uo_id, Notification.kind == "snooze")
        )
        return list(rows)


async def test_snooze_creates_tomorrow_10_and_removes_button(fake_max, clock):
    uo_id, _ = await _one_d7()

    await process_update(_snooze_update(uo_id), fake_max)

    snoozes = await _snoozes(uo_id)
    assert len(snoozes) == 1
    assert snoozes[0].status == "pending"
    assert snoozes[0].send_at.replace(tzinfo=UTC) == utc(2026, 10, 22, 7)  # завтра 10:00 МСК
    assert [m["text"] for m in fake_max.sent] == ["Хорошо, напомню завтра в 10:00."]
    assert len(fake_max.edited) == 1
    edit = fake_max.edited[0]
    assert edit["message_id"] == "mid-1"
    assert edit["text"] == (
        f"Через 7 дней, 28 октября — {YEARLY}. Если опоздать: Тестовый текст о пене."
    )
    assert _payloads(edit["attachments"]) == [
        [f"r:done:obligation:{uo_id}"],
        [f"r:howto:obligation:{uo_id}"],
    ]
    assert await _events("reminder_clicked") == [{"kind": "d7", "action": "snooze"}]


async def test_snooze_only_once(fake_max, clock):
    uo_id, _ = await _one_d7()

    await process_update(_snooze_update(uo_id, "cb-1"), fake_max)
    await process_update(_snooze_update(uo_id, "cb-2"), fake_max)

    assert len(await _snoozes(uo_id)) == 1
    assert [m["text"] for m in fake_max.sent] == ["Хорошо, напомню завтра в 10:00."] * 2


async def test_snooze_not_created_when_tomorrow_is_d1(fake_max, clock):
    uo_id, _ = await _one_d7(due=date(2026, 10, 23))  # завтра, 22-го, — день d1

    await process_update(_snooze_update(uo_id), fake_max)

    assert await _snoozes(uo_id) == []
    assert [m["text"] for m in fake_max.sent] == ["Хорошо, напомню завтра в 10:00."]
    assert len(fake_max.edited) == 1  # кнопка пропадает и в этом случае


async def test_snooze_without_mid_still_created(fake_max, clock):
    uo_id, _ = await _one_d7()

    await process_update(_snooze_update(uo_id, mid=None), fake_max)

    assert fake_max.edited == []
    assert len(await _snoozes(uo_id)) == 1


async def test_snooze_on_marked_item_replies_already(fake_max, clock):
    """DEBT-1 (#67): по уже отмеченному событию — «уже отмечено» (экран 8), а не тишина."""
    uo_id, _ = await _one_d7(done=True)

    await process_update(_snooze_update(uo_id), fake_max)

    assert await _snoozes(uo_id) == []
    assert [m["text"] for m in fake_max.sent] == ["Это событие уже отмечено 1 января."]
    assert fake_max.edited == []
    assert await _events("reminder_clicked") == [{"kind": "d7", "action": "snooze"}]


async def test_snooze_on_missing_item_does_nothing(fake_max, clock):
    """Удалено, чужое или пропало из справочника — по-прежнему тишина, snooze не создаём."""
    await process_update(_snooze_update(999_999), fake_max)

    assert await _snoozes(999_999) == []
    assert fake_max.sent == []
    assert fake_max.edited == []
    assert await _events("reminder_clicked") == [{"kind": "d7", "action": "snooze"}]


async def test_snooze_edit_happens_outside_write_transaction(clock):
    """Правка сообщения — сетевой вызов — не держит блокировку записи SQLite."""
    uo_id, _ = await _one_d7()

    class ProbeEditMax(FakeMax):
        async def edit_message(self, message_id, text, **kwargs):
            async with SessionLocal() as probe:
                probe.add(Event(user_id=None, name="probe", props={}))
                await asyncio.wait_for(probe.commit(), timeout=2)
            return await super().edit_message(message_id, text, **kwargs)

    max_client = ProbeEditMax()
    await process_update(_snooze_update(uo_id), max_client)

    assert len(max_client.edited) == 1
    assert len(await _snoozes(uo_id)) == 1


async def test_snooze_is_sent_next_day_without_snooze_button(fake_max, clock):
    uo_id, _ = await _one_d7()
    await tick(D7_AT, fake_max)  # само d7
    await process_update(_snooze_update(uo_id), fake_max)
    fake_max.sent.clear()

    await tick(utc(2026, 10, 22, 7), fake_max)

    assert [m["text"] for m in fake_max.sent] == [f"Напоминаю: 28 октября — {YEARLY}."]
    assert _payloads(fake_max.sent[0]["attachments"]) == [
        [f"r:done:obligation:{uo_id}"],
        [f"r:howto:obligation:{uo_id}"],
    ]
    assert (await _events("reminder_sent"))[-1] == {
        "kind": "snooze",
        "item_id": uo_id,
        "item_type": "obligation",
        "grouped": False,
    }


# --- #97: вид выключили или событие отметили, пока напоминание «в пути» ------------------------


async def _save_settings(now: datetime, user_id: int = USER, **changes) -> None:
    """Как `PUT /api/profile/settings`: Profile.reminders и пересчёт — одной транзакцией."""
    async with SessionLocal() as session:
        profile = await session.get(Profile, user_id)
        profile.reminders = {**(profile.reminders or {}), **changes}
        await resync_user_notifications(
            session,
            user_id,
            tz=profile.timezone,
            settings=profile.reminders,
            reference=loader.get_reference(),
            now=now,
        )
        await session.commit()


async def _mark_done(uo_id: int, now: datetime) -> None:
    """Как отметка (marks.py): done_at и отмена всех pending события."""
    async with SessionLocal() as session:
        uo = await session.get(UserObligation, uo_id)
        uo.done_at = now
        await cancel_pending(session, "obligation", uo_id)
        await session.commit()


class InFlightMax(FailingMax):
    """Пока сообщение «в пути» (после «забрать», до «записать»), выполняет `action`
    отдельной транзакцией — как сохранение настроек или отметка из мини-аппа."""

    def __init__(self, action, fails: int = 0) -> None:
        super().__init__(fails=fails)
        self.action = action

    async def send_message(self, text, **kwargs):
        if self.action is not None:
            action, self.action = self.action, None
            await action()
        return await super().send_message(text, **kwargs)


async def test_turned_off_in_flight_sent_is_recorded():
    """Сообщение ушло, хотя пересчёт отменил строку: `sent` и `reminder_sent`, повторов нет."""
    uo_id, nid = await _one_d7()
    max_client = InFlightMax(lambda: _save_settings(D7_AT, d7=False))

    await tick(D7_AT, max_client)

    n = await _get(nid)
    assert (n.status, n.attempts) == ("sent", 1)
    assert n.send_at.replace(tzinfo=UTC) == D7_AT
    assert await _events("reminder_sent") == [
        {"kind": "d7", "item_id": uo_id, "item_type": "obligation", "grouped": False}
    ]
    for hours in (1, 2, 5):
        await tick(D7_AT + timedelta(hours=hours), max_client)
    assert len(max_client.sent) == 1


async def test_turned_off_in_flight_send_failed_no_retry():
    _, nid = await _one_d7()
    max_client = InFlightMax(lambda: _save_settings(D7_AT, d7=False), fails=1)

    await tick(D7_AT, max_client)
    assert (await _get(nid)).status == "cancelled"

    for hours in (1, 2, 5):
        await tick(D7_AT + timedelta(hours=hours), max_client)
    assert max_client.calls == 1  # повтора по выключенному виду нет
    assert (await _get(nid)).status == "cancelled"
    assert await _events("reminder_sent") == []


async def test_marked_in_flight_sent_is_recorded():
    uo_id, nid = await _one_d7()
    max_client = InFlightMax(lambda: _mark_done(uo_id, D7_AT))

    await tick(D7_AT, max_client)

    assert (await _get(nid)).status == "sent"
    assert len(await _events("reminder_sent")) == 1
    assert len(max_client.sent) == 1


async def test_failed_then_turned_off_no_retry():
    """Первая попытка упала (строка ждёт повтора), потом вид выключили — повтора нет."""
    _, nid = await _one_d7()
    max_client = FailingMax(fails=1)

    await tick(D7_AT, max_client)
    assert ((await _get(nid)).status, (await _get(nid)).attempts) == ("pending", 1)

    await _save_settings(D7_AT + timedelta(minutes=10), d7=False)
    await tick(D7_AT + timedelta(hours=1), max_client)

    assert max_client.calls == 1
    assert (await _get(nid)).status == "cancelled"


async def test_past_pending_of_turned_off_kind_is_not_sent(fake_max):
    """Сервер стоял: d7 на 10:00 ещё не забрано, в 10:30 d7 выключили — тик его не шлёт."""
    _, nid = await _one_d7()

    await _save_settings(D7_AT + timedelta(minutes=30), d7=False)
    await tick(D7_AT + timedelta(minutes=31), fake_max)

    assert fake_max.sent == []
    assert (await _get(nid)).status == "cancelled"


async def test_past_d1_still_sent_after_hour_change(fake_max):
    """d1 не выключается: смена часа, когда его время уже прошло, его не отменяет (#85)."""
    d1_at = utc(2026, 10, 27, 7)  # 10:00 МСК
    async with SessionLocal() as session:
        uo = await _uo(session)
        nid = (await _notify(session, "obligation", uo.id, "d1", d1_at)).id
        await session.commit()

    await _save_settings(d1_at + timedelta(minutes=30), hour=9)  # 9:00 МСК уже прошло
    await tick(d1_at + timedelta(minutes=31), fake_max)

    assert [m["text"] for m in fake_max.sent] == [
        f"Завтра, 28 октября — {YEARLY}. Это последний день срока."
    ]
    assert (await _get(nid)).status == "sent"
