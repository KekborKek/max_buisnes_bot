"""app/calendar/marks.py: отметка, снятие, «Неверный срок» (reminders.md, правило 1).

Справочник — фикстура backend/tests/fixtures/obligations.yaml (ТЕСТОВЫЕ ДАННЫЕ).
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.calendar import marks
from app.core.db import SessionLocal
from app.core.models import Profile, Task, WrongDateReport
from tests.test_done import NOW, USER, add_notification, add_task, add_uo, notifications, utc

pytestmark = pytest.mark.usefixtures("fixture_reference")


async def _item(session, reference, item_type: str, item_id: int, user_id: int = USER):
    return await marks.load_own_item(
        session, reference, user_id=user_id, item_type=item_type, item_id=item_id
    )


async def test_mark_cancels_pending_and_is_idempotent(fixture_reference):
    uo_id = await add_uo()
    await add_notification(uo_id, "d7", utc(2026, 10, 21, 7), status="sent")
    await add_notification(uo_id, "d1", utc(2026, 10, 27, 7))
    await add_notification(uo_id, "overdue", utc(2026, 10, 29, 7))

    async with SessionLocal() as session:
        item = await _item(session, fixture_reference, "obligation", uo_id)
        assert await marks.mark_done(session, item, now=NOW) is True
        assert item.done_at is not None
        assert await marks.mark_done(session, item, now=utc(2026, 10, 22)) is False
        await session.commit()

    assert await notifications(uo_id) == {
        "d7": ["sent"],
        "d1": ["cancelled"],
        "overdue": ["cancelled"],
    }


async def test_unmark_returns_future_notifications(fixture_reference):
    uo_id = await add_uo()
    await add_notification(uo_id, "d1", utc(2026, 10, 27, 7))
    await add_notification(uo_id, "overdue", utc(2026, 10, 29, 7))

    async with SessionLocal() as session:
        item = await _item(session, fixture_reference, "obligation", uo_id)
        await marks.mark_done(session, item, now=NOW)
        profile = await session.get(Profile, USER)
        assert await marks.unmark_done(session, item, profile=profile, now=NOW) is True
        assert item.done_at is None
        assert await marks.unmark_done(session, item, profile=profile, now=NOW) is False
        await session.commit()

    # d30 и d7 для 28 октября к 21 октября 11:00 МСК уже в прошлом — не создаются.
    assert await notifications(uo_id) == {
        "d1": ["cancelled", "pending"],
        "overdue": ["cancelled", "pending"],
    }


async def test_mark_and_unmark_task(fixture_reference):
    task_id = await add_task("Оплатить аренду", date(2026, 10, 30))
    await add_notification(task_id, "task", utc(2026, 10, 29, 7), item_type="task")

    async with SessionLocal() as session:
        item = await _item(session, fixture_reference, "task", task_id)
        assert await marks.mark_done(session, item, now=NOW) is True
        await session.flush()
        assert await marks.unmark_done(session, item, profile=None, now=NOW) is True
        await session.commit()

    assert await notifications(task_id, "task") == {"task": ["cancelled", "pending"]}


async def test_load_own_item_rejects_foreign_deleted_and_unknown(fixture_reference):
    foreign = await add_uo(user_id=777)
    unknown = await add_uo(ob="removed_from_catalog")
    task_id = await add_task("Оплатить аренду", date(2026, 10, 30))
    async with SessionLocal() as session:
        (await session.get(Task, task_id)).deleted_at = NOW
        await session.commit()

    async with SessionLocal() as session:
        assert await _item(session, fixture_reference, "obligation", foreign) is None
        assert await _item(session, fixture_reference, "obligation", unknown) is None
        assert await _item(session, fixture_reference, "task", task_id) is None
        assert await _item(session, fixture_reference, "event", 1) is None


async def test_report_wrong_date_once(fixture_reference):
    uo_id = await add_uo()

    async with SessionLocal() as session:
        item = await _item(session, fixture_reference, "obligation", uo_id)
        assert await marks.report_wrong_date(session, user_id=USER, uo=item.row, now=NOW) is True
        assert await marks.report_wrong_date(session, user_id=USER, uo=item.row, now=NOW) is False
        await session.commit()

    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(WrongDateReport)) == 1
        report = await session.scalar(select(WrongDateReport))
        assert (report.obligation_id, report.due_date) == ("test_yearly", date(2026, 10, 28))
        assert report.created_at is not None
