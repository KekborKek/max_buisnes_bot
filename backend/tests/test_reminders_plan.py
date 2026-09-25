"""T4 (#45): расписание уведомлений — docs/spec/reminders.md, D10, D11.

Даты и пояса здесь — ТЕСТОВЫЕ ДАННЫЕ, не налоговые сроки. Ожидаемые моменты отправки
записаны явно, а не вычислены той же формулой, что в коде.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.calendar.reminders import (
    PlannedNotification,
    cancel_pending,
    plan_obligation_notifications,
    plan_task_notification,
    send_at_utc,
    sync_obligation_notifications,
    sync_task_notification,
)
from app.core.db import SessionLocal
from app.core.models import Notification, Task, User, UserObligation

MSK = "Europe/Moscow"
DUE = date(2026, 10, 28)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- send_at_utc ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tz", "expected"),
    [
        ("Europe/Moscow", utc(2026, 10, 21, 7)),
        ("Europe/Kaliningrad", utc(2026, 10, 21, 8)),
        ("Asia/Vladivostok", utc(2026, 10, 21, 0)),
        ("Asia/Kamchatka", utc(2026, 10, 20, 22)),
    ],
)
def test_send_at_utc_is_10_local(tz, expected):
    at = send_at_utc(date(2026, 10, 21), 10, tz)
    assert at == expected
    assert at.utcoffset().total_seconds() == 0


def test_vladivostok_and_kaliningrad_differ():
    vl = send_at_utc(date(2026, 10, 21), 10, "Asia/Vladivostok")
    kgd = send_at_utc(date(2026, 10, 21), 10, "Europe/Kaliningrad")
    assert vl != kgd
    assert (kgd - vl).total_seconds() == 8 * 3600


# --- plan_obligation_notifications ---------------------------------------------------------------


def test_needs_prep_gets_d30_d7_d1_overdue():
    planned = plan_obligation_notifications(DUE, needs_prep=True, tz=MSK, now=LONG_AGO)
    assert planned == [
        PlannedNotification("d30", utc(2026, 9, 28, 7)),
        PlannedNotification("d7", utc(2026, 10, 21, 7)),
        PlannedNotification("d1", utc(2026, 10, 27, 7)),
        PlannedNotification("overdue", utc(2026, 10, 29, 7)),  # D10: на следующий день, 10:00
    ]


def test_d30_only_for_needs_prep():
    planned = plan_obligation_notifications(DUE, needs_prep=False, tz=MSK, now=LONG_AGO)
    assert [p.kind for p in planned] == ["d7", "d1", "overdue"]


def test_settings_turn_off_d30_and_d7_but_not_d1():
    planned = plan_obligation_notifications(
        DUE, needs_prep=True, tz=MSK, now=LONG_AGO, d30=False, d7=False
    )
    assert [p.kind for p in planned] == ["d1", "overdue"]


def test_hour_from_settings():
    planned = plan_obligation_notifications(DUE, needs_prep=False, tz=MSK, now=LONG_AGO, hour=18)
    assert planned[0] == PlannedNotification("d7", utc(2026, 10, 21, 15))


@pytest.mark.parametrize(
    ("now", "kinds"),
    [
        (utc(2026, 9, 28, 6, 59), ["d30", "d7", "d1", "overdue"]),
        (utc(2026, 9, 28, 7), ["d7", "d1", "overdue"]),  # send_at == now — уже не создаём
        (utc(2026, 10, 15), ["d7", "d1", "overdue"]),
        (utc(2026, 10, 22), ["d1", "overdue"]),
        (utc(2026, 10, 28, 12), ["overdue"]),
        (utc(2026, 10, 29, 7), []),
    ],
)
def test_past_notifications_are_not_planned(now, kinds):
    planned = plan_obligation_notifications(DUE, needs_prep=True, tz=MSK, now=now)
    assert [p.kind for p in planned] == kinds
    assert all(p.send_at > now for p in planned)


# --- plan_task_notification -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (0, utc(2026, 10, 28, 6)),
        (1, utc(2026, 10, 27, 6)),
        (3, utc(2026, 10, 25, 6)),
        (7, utc(2026, 10, 21, 6)),
    ],
)
def test_task_single_notification_with_form_offset(offset, expected):
    planned = plan_task_notification(
        DUE, remind_offset_days=offset, remind_hour=9, tz=MSK, now=LONG_AGO
    )
    assert planned == PlannedNotification("task", expected)


def test_task_notification_in_past_is_none():
    planned = plan_task_notification(
        DUE, remind_offset_days=7, remind_hour=10, tz=MSK, now=utc(2026, 10, 22)
    )
    assert planned is None


# --- БД: cancel_pending и синхронизация -----------------------------------------------------------

USER_ID = 101


async def _user_obligation(session, *, due=DUE, user_id=USER_ID) -> UserObligation:
    if await session.get(User, user_id) is None:
        session.add(User(user_id=user_id))
        await session.flush()  # связей в ORM нет — порядок вставки задаём сами
    uo = UserObligation(
        user_id=user_id,
        obligation_id="test_yearly",
        rule_version=1,
        original_date=due,
        due_date=due,
    )
    session.add(uo)
    await session.flush()
    return uo


async def _notifications(session, item_type, item_id) -> list[Notification]:
    rows = await session.scalars(
        select(Notification)
        .where(Notification.item_type == item_type, Notification.item_id == item_id)
        .order_by(Notification.id)
    )
    return list(rows)


def _pending(rows) -> list[str]:
    return sorted(n.kind for n in rows if n.status == "pending")


async def _sync(session, uo, *, now=LONG_AGO, tz=MSK, settings=None, needs_prep=True):
    await sync_obligation_notifications(
        session, uo, needs_prep=needs_prep, tz=tz, settings=settings, now=now
    )
    await session.flush()


async def test_cancel_pending_touches_only_pending_of_this_item():
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        other = await _user_obligation(session, due=date(2026, 12, 28))
        await _sync(session, uo)
        await _sync(session, other)
        rows = await _notifications(session, "obligation", uo.id)
        rows[0].status = "sent"
        await session.flush()

        assert await cancel_pending(session, "obligation", uo.id) == 3

        statuses = sorted(n.status for n in await _notifications(session, "obligation", uo.id))
        assert statuses == ["cancelled", "cancelled", "cancelled", "sent"]
        assert _pending(await _notifications(session, "obligation", other.id)) == [
            "d1",
            "d30",
            "d7",
            "overdue",
        ]
        assert await cancel_pending(session, "obligation", uo.id) == 0


async def test_sync_twice_creates_no_duplicates():
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo)
        first = [
            (n.id, n.kind, n.status) for n in await _notifications(session, "obligation", uo.id)
        ]
        await _sync(session, uo)
        second = [
            (n.id, n.kind, n.status) for n in await _notifications(session, "obligation", uo.id)
        ]
        assert first == second
        assert len(first) == 4


async def test_mark_cancels_d1_and_overdue_unmark_returns_them():
    now = utc(2026, 10, 22)  # d30 и d7 уже в прошлом
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo, now=now)
        assert _pending(await _notifications(session, "obligation", uo.id)) == ["d1", "overdue"]

        uo.done_at = now
        assert await cancel_pending(session, "obligation", uo.id) == 2
        await _sync(session, uo, now=now)  # повторный вызов на отмеченном ничего не создаёт
        assert _pending(await _notifications(session, "obligation", uo.id)) == []

        uo.done_at = None
        await _sync(session, uo, now=now)
        rows = await _notifications(session, "obligation", uo.id)
        assert _pending(rows) == ["d1", "overdue"]
        assert all(n.send_at.replace(tzinfo=UTC) > now for n in rows if n.status == "pending")


async def test_sent_kind_is_not_recreated():
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo)
        for n in await _notifications(session, "obligation", uo.id):
            if n.kind == "d7":
                n.status = "sent"
        await _sync(session, uo, tz="Europe/Kaliningrad")
        rows = await _notifications(session, "obligation", uo.id)
        assert sorted(n.kind for n in rows if n.kind == "d7") == ["d7"]
        assert _pending(rows) == ["d1", "d30", "overdue"]


async def test_timezone_change_moves_pending_without_duplicates():
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo)
        await _sync(session, uo, tz="Asia/Vladivostok")
        rows = await _notifications(session, "obligation", uo.id)
        assert len(rows) == 4
        d7 = next(n for n in rows if n.kind == "d7")
        assert d7.send_at.replace(tzinfo=UTC) == utc(2026, 10, 21, 0)


async def test_turned_off_d30_is_cancelled_snooze_untouched():
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo)
        session.add(
            Notification(
                user_id=USER_ID,
                item_type="obligation",
                item_id=uo.id,
                kind="snooze",
                send_at=utc(2026, 10, 22, 7),
                status="pending",
            )
        )
        await session.flush()
        await _sync(session, uo, settings={"d30": False, "d7": True, "hour": 10})
        assert _pending(await _notifications(session, "obligation", uo.id)) == [
            "d1",
            "d7",
            "overdue",
            "snooze",
        ]


async def test_duplicate_pending_is_healed():
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo)
        session.add(
            Notification(
                user_id=USER_ID,
                item_type="obligation",
                item_id=uo.id,
                kind="d1",
                send_at=utc(2026, 10, 27, 7),
                status="pending",
            )
        )
        await session.flush()
        await _sync(session, uo)
        assert _pending(await _notifications(session, "obligation", uo.id)) == [
            "d1",
            "d30",
            "d7",
            "overdue",
        ]


# --- #85: будущее pending отменяется только у выключенного вида ----------------------------------
# DUE = 28.10.2026: d1 — 27.10 10:00 МСК = 07:00 UTC; во Владивостоке 10:00 = 00:00 UTC.
D1_DAY_MORNING = utc(2026, 10, 27, 5)  # 08:00 МСК: d1 по Москве впереди, по Владивостоку прошёл


def _add(session, uo, kind, send_at, *, status="pending", attempts=0) -> None:
    session.add(
        Notification(
            user_id=USER_ID,
            item_type="obligation",
            item_id=uo.id,
            kind=kind,
            send_at=send_at,
            status=status,
            attempts=attempts,
        )
    )


async def test_enabled_kind_new_time_passed_keeps_exactly_one_copy():
    """(а) Сменили пояс в день d1: две будущие pending-копии d1 → остаётся ровно одна."""
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        _add(session, uo, "d1", utc(2026, 10, 27, 7))
        _add(session, uo, "d1", utc(2026, 10, 27, 7))
        await session.flush()

        await _sync(session, uo, now=D1_DAY_MORNING, tz="Asia/Vladivostok")

        d1 = [n for n in await _notifications(session, "obligation", uo.id) if n.kind == "d1"]
        assert [n.status for n in d1] == ["pending", "cancelled"]
        assert d1[0].send_at.replace(tzinfo=UTC) == utc(2026, 10, 27, 7)  # старое время


async def test_d30_without_needs_prep_is_cancelled():
    """(б) needs_prep=false: pending d30 — выключенный вид, отменяется."""
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo, needs_prep=True)
        await _sync(session, uo, needs_prep=False)
        rows = await _notifications(session, "obligation", uo.id)
        assert [n.status for n in rows if n.kind == "d30"] == ["cancelled"]
        assert _pending(rows) == ["d1", "d7", "overdue"]


async def test_leased_enabled_kind_survives_rebuild():
    """(в) «Аренда» планировщика (attempts=1, send_at = now + 1 ч) при пересборке не отменяется."""
    now = utc(2026, 10, 21, 7, 0, 30)  # d7 только что забран тиком
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        await _sync(session, uo)
        for n in await _notifications(session, "obligation", uo.id):
            if n.kind == "d30":
                n.status = "sent"
            if n.kind == "d7":
                n.attempts = 1
                n.send_at = utc(2026, 10, 21, 8, 0, 30)
        await session.flush()

        await _sync(session, uo, now=now)  # пересборка: пояс и час те же

        d7 = next(n for n in await _notifications(session, "obligation", uo.id) if n.kind == "d7")
        assert (d7.status, d7.attempts) == ("pending", 1)
        assert d7.send_at.replace(tzinfo=UTC) == utc(2026, 10, 21, 8, 0, 30)


async def test_future_copy_of_sent_kind_is_cancelled():
    """(г) d1 уже sent, есть будущая pending-копия, новое время прошло → копия отменена."""
    async with SessionLocal() as session:
        uo = await _user_obligation(session)
        _add(session, uo, "d1", utc(2026, 10, 26, 7), status="sent")
        _add(session, uo, "d1", utc(2026, 10, 27, 7))
        await session.flush()

        await _sync(session, uo, now=D1_DAY_MORNING, tz="Asia/Vladivostok")

        d1 = [n for n in await _notifications(session, "obligation", uo.id) if n.kind == "d1"]
        assert [n.status for n in d1] == ["sent", "cancelled"]


# --- задачи ------------------------------------------------------------------------------------


async def _task(session, *, offset=1, hour=10) -> Task:
    session.add(User(user_id=USER_ID))
    await session.flush()  # связей в ORM нет — порядок вставки задаём сами
    task = Task(
        user_id=USER_ID,
        title="Тестовая задача",
        due_date=DUE,
        remind_offset_days=offset,
        remind_hour=hour,
    )
    session.add(task)
    await session.flush()
    return task


async def test_task_one_notification_replanning_cancels_old():
    async with SessionLocal() as session:
        task = await _task(session, offset=1)
        await sync_task_notification(session, task, tz=MSK, now=LONG_AGO)
        await sync_task_notification(session, task, tz=MSK, now=LONG_AGO)
        await session.flush()
        rows = await _notifications(session, "task", task.id)
        assert [(n.kind, n.status) for n in rows] == [("task", "pending")]
        assert rows[0].send_at.replace(tzinfo=UTC) == utc(2026, 10, 27, 7)

        task.remind_offset_days = 7
        await sync_task_notification(session, task, tz=MSK, now=LONG_AGO)
        await session.flush()
        rows = await _notifications(session, "task", task.id)
        assert [(n.status, n.send_at.replace(tzinfo=UTC)) for n in rows] == [
            ("cancelled", utc(2026, 10, 27, 7)),
            ("pending", utc(2026, 10, 21, 7)),
        ]


@pytest.mark.parametrize("field", ["done_at", "deleted_at"])
async def test_done_or_deleted_task_has_no_pending(field):
    async with SessionLocal() as session:
        task = await _task(session)
        await sync_task_notification(session, task, tz=MSK, now=LONG_AGO)
        setattr(task, field, LONG_AGO)
        await sync_task_notification(session, task, tz=MSK, now=LONG_AGO)
        await session.flush()
        assert _pending(await _notifications(session, "task", task.id)) == []


async def test_task_in_past_gets_no_notification():
    async with SessionLocal() as session:
        task = await _task(session, offset=7)
        await sync_task_notification(session, task, tz=MSK, now=utc(2026, 10, 22))
        await session.flush()
        assert await _notifications(session, "task", task.id) == []
