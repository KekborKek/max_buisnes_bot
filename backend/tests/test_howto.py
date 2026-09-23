"""Экран 7: «Как сделать» и «Неверный срок» из бота (T7).

Справочник — фикстура backend/tests/fixtures/obligations.yaml (ТЕСТОВЫЕ ДАННЫЕ).
"""

import asyncio
import dataclasses
from datetime import date

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.calendar import loader
from app.calendar.types import Catalog, Reference
from app.core.db import SessionLocal
from app.core.models import Event, WrongDateReport
from tests.conftest import FakeMax
from tests.test_done import (
    NOW,
    USER,
    YEARLY,
    add_notification,
    add_task,
    add_uo,
    callback,
    clock,  # noqa: F401 — фикстура часов для usefixtures
    events,
    payloads,
    sent_texts,
    texts,
    utc,
)

pytestmark = pytest.mark.usefixtures("fixture_reference", "clock")

HOWTO_TEXT = (
    f"**{YEARLY}**, до 28 октября.\n\n"
    "1. Тестовый шаг 1.\n"
    "2. Тестовый шаг 2 — до {notice_date}.\n"  # D28: правила нет — остаётся как есть
    "3. Тестовый шаг 3 — до 28 октября.\n\n"
    "Основание: Тестовая норма №1. Сверено 01.01.2026. "
    "Суммы не считаем, это не налоговая консультация."
)


async def reports() -> list[WrongDateReport]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(WrongDateReport)))


async def test_howto_from_reminder(fake_max):
    uo_id = await add_uo()
    await add_notification(uo_id, "d1", utc(2026, 10, 21, 7), status="sent")

    await process_update(callback(f"r:howto:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == [HOWTO_TEXT]
    attachments = fake_max.sent[0]["attachments"]
    assert payloads(attachments) == [
        ["link:https://example.invalid/howto"],
        [f"h:done:obligation:{uo_id}", f"h:wrong:obligation:{uo_id}"],
    ]
    assert texts(attachments) == [["Открыть тест"], ["Отметить выполненным", "Неверный срок"]]
    assert await events("reminder_clicked") == [{"kind": "d1", "action": "howto"}]
    assert await events("howto_opened") == [
        {"item_id": uo_id, "item_type": "obligation", "source": "reminder"}
    ]


async def test_howto_date_of_next_year_has_year(fake_max):
    uo_id = await add_uo(due=date(2027, 1, 25))

    await process_update(callback(f"r:howto:obligation:{uo_id}"), fake_max)

    text = sent_texts(fake_max)[0]
    assert text.startswith(f"**{YEARLY}**, до 25 января 2027.")
    assert "3. Тестовый шаг 3 — до 25 января 2027." in text


async def test_howto_on_marked_item_says_already(fake_max):
    uo_id = await add_uo(done_at=NOW)

    await process_update(callback(f"r:howto:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == ["Это событие уже отмечено 21 октября."]
    assert await events("howto_opened") == []


async def test_howto_for_task_is_unreachable(fake_max):
    task_id = await add_task("Оплатить аренду", date(2026, 10, 24))

    await process_update(callback(f"r:howto:task:{task_id}"), fake_max)

    assert fake_max.sent == []
    assert await events("howto_opened") == []


async def test_howto_without_steps_shows_source_link(fake_max, fixture_reference, monkeypatch):
    """Экран 7, «Состояния»: у события нет шагов — no_steps и ссылка на source_url."""
    obligations = tuple(
        dataclasses.replace(ob, howto_steps=()) if ob.id == "test_yearly" else ob
        for ob in fixture_reference.catalog.obligations
    )
    reference = Reference(
        catalog=Catalog(version=fixture_reference.catalog.version, obligations=obligations),
        workdays=fixture_reference.workdays,
        nds=fixture_reference.nds,
    )
    monkeypatch.setattr(loader, "get_reference", lambda: reference)
    uo_id = await add_uo()

    await process_update(callback(f"r:howto:obligation:{uo_id}"), fake_max)

    assert sent_texts(fake_max) == [
        "По этому событию у меня пока нет порядка действий. "
        "Срок — 28 октября, основание — Тестовая норма №1."
    ]
    assert payloads(fake_max.sent[0]["attachments"])[0] == [
        "link:https://example.invalid/test-yearly"
    ]


# --- «Неверный срок» -------------------------------------------------------------------------


async def test_wrong_date_creates_report_and_removes_button(fake_max):
    uo_id = await add_uo()

    await process_update(callback(f"h:wrong:obligation:{uo_id}", mid="mid-7"), fake_max)

    rows = await reports()
    assert [(r.user_id, r.obligation_id, r.due_date) for r in rows] == [
        (USER, "test_yearly", date(2026, 10, 28))
    ]
    assert sent_texts(fake_max) == ["Спасибо, проверим и исправим. Вашу отметку записал."]
    assert len(fake_max.edited) == 1
    edit = fake_max.edited[0]
    assert edit["message_id"] == "mid-7"
    assert edit["text"] == HOWTO_TEXT
    assert payloads(edit["attachments"]) == [
        ["link:https://example.invalid/howto"],
        [f"h:done:obligation:{uo_id}"],
    ]
    assert await events("wrong_date_reported") == [
        {"item_id": uo_id, "item_type": "obligation", "source": "howto"}
    ]


async def test_wrong_date_only_once(fake_max):
    uo_id = await add_uo()

    await process_update(callback(f"h:wrong:obligation:{uo_id}", "cb-1"), fake_max)
    await process_update(callback(f"h:wrong:obligation:{uo_id}", "cb-2"), fake_max)

    assert len(await reports()) == 1
    assert len(await events("wrong_date_reported")) == 1


async def test_wrong_date_parallel_presses_once():
    uo_id = await add_uo()
    fakes = [FakeMax(), FakeMax()]

    await asyncio.gather(
        *(
            process_update(callback(f"h:wrong:obligation:{uo_id}", f"cb-{i}"), fake)
            for i, fake in enumerate(fakes)
        )
    )

    assert len(await reports()) == 1
    assert len(await events("wrong_date_reported")) == 1


async def test_wrong_date_edit_happens_outside_write_transaction():
    uo_id = await add_uo()

    class ProbeEditMax(FakeMax):
        async def edit_message(self, message_id, text, **kwargs):
            async with SessionLocal() as probe:
                probe.add(Event(user_id=None, name="probe", props={}))
                await asyncio.wait_for(probe.commit(), timeout=2)
            return await super().edit_message(message_id, text, **kwargs)

    max_client = ProbeEditMax()
    await process_update(callback(f"h:wrong:obligation:{uo_id}"), max_client)

    assert len(max_client.edited) == 1
    assert len(await reports()) == 1


async def test_done_button_from_howto_marks(fake_max):
    uo_id = await add_uo()

    await process_update(callback(f"r:howto:obligation:{uo_id}", "cb-1"), fake_max)
    done_payload = payloads(fake_max.sent[0]["attachments"])[1][0]
    await process_update(callback(done_payload, "cb-2"), fake_max)

    assert sent_texts(fake_max)[1].startswith(f"Отметил: {YEARLY} — выполнено.")
    assert [e["source"] for e in await events("item_done")] == ["howto"]
