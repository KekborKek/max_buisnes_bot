"""SHARE: «Поделиться сроком» — приглашение по ссылке и добавление себе в календарь.

«Сейчас» — 23.09.2026 10:00 по Москве (фикстура miniapp_api). Справочник — ТЕСТОВЫЕ ДАННЫЕ
из backend/tests/fixtures.
"""

import re
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.api.shares import task_title
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Notification, SharedItem, SharedItemAccept, Task
from tests.test_api_calendar import add_obligation, add_task, add_user, events

SENDER = 701
RECIPIENT = 702
NEWCOMER = 703  # ни профиля, ни строки User: мини-апп открыт по ссылке впервые (экран 18)
BOT = "test_calendar_bot"


@pytest.fixture(autouse=True)
def bot_username(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT)


async def share(api, item_type: str, item_id: int, user_id: int = SENDER):
    return await api.request(
        "POST", "/api/shares", user_id, json={"item_type": item_type, "item_id": item_id}
    )


async def task_count(user_id: int) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(Task).where(Task.user_id == user_id))


async def task_notifications(task_id: int) -> list[tuple[str, str, datetime]]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification).where(
                Notification.item_type == "task", Notification.item_id == task_id
            )
        )
        return [(n.kind, n.status, n.send_at.replace(tzinfo=UTC)) for n in rows]


# --- Доступ -------------------------------------------------------------------------------------

ENDPOINTS = [
    ("POST", "/api/shares", {"item_type": "task", "item_id": 1}),
    ("GET", "/api/shares/abcdefghijkl", None),
    ("POST", "/api/shares/abcdefghijkl/accept", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
async def test_share_endpoints_require_init_data(miniapp_api, method, path, body):
    r = await miniapp_api.request(method, path, None, json=body)
    assert r.status_code == 401


@pytest.mark.parametrize("item_type", ["task", "obligation"])
async def test_share_foreign_item_is_404(miniapp_api, item_type):
    await add_user(SENDER)
    await add_user(RECIPIENT)
    if item_type == "task":
        item_id = await add_task(SENDER, date(2026, 10, 5))
    else:
        item_id = await add_obligation(SENDER, date(2026, 10, 28))
    r = await share(miniapp_api, item_type, item_id, user_id=RECIPIENT)
    assert r.status_code == 404
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(SharedItem)) == 0


async def test_share_deleted_task_is_404(miniapp_api):
    await add_user(SENDER)
    task_id = await add_task(
        SENDER, date(2026, 10, 5), deleted_at=datetime(2026, 9, 22, tzinfo=UTC)
    )
    assert (await share(miniapp_api, "task", task_id)).status_code == 404


# --- Создание приглашения -----------------------------------------------------------------------


async def test_share_task_returns_code_and_deeplink(miniapp_api):
    await add_user(SENDER)
    task_id = await add_task(SENDER, date(2026, 10, 5), title="Оплатить аренду")
    r = await share(miniapp_api, "task", task_id)
    assert r.status_code == 200
    body = r.json()
    code = body["code"]
    # алфавит startapp — [A-Za-z0-9_-] (dev.max.ru/docs/webapps/introduction)
    assert re.fullmatch(r"[A-Za-z0-9_-]{12}", code)
    assert body == {
        "code": code,
        "link": f"https://max.ru/{BOT}?startapp=share_{code}",
        "title": "Оплатить аренду",
        "due_date": "2026-10-05",
    }


async def test_share_same_item_twice_reuses_code(miniapp_api):
    """Мини-апп создаёт приглашение при каждом открытии карточки — строки не плодятся."""
    await add_user(SENDER)
    task_id = await add_task(SENDER, date(2026, 10, 5))
    first = (await share(miniapp_api, "task", task_id)).json()["code"]
    second = (await share(miniapp_api, "task", task_id)).json()["code"]
    assert first == second
    # Перенесли задачу — новое приглашение с новой датой, старое остаётся как было.
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        task.due_date = date(2026, 10, 9)
        await s.commit()
    third = (await share(miniapp_api, "task", task_id)).json()
    assert third["code"] != first and third["due_date"] == "2026-10-09"


async def test_share_obligation_uses_catalog_title_and_due_date(miniapp_api):
    await add_user(SENDER)
    uo_id = await add_obligation(SENDER, date(2026, 10, 28), original=date(2026, 10, 25))
    body = (await share(miniapp_api, "obligation", uo_id)).json()
    assert body["title"] == "Тестовое годовое обязательство"
    assert body["due_date"] == "2026-10-28"  # после переноса на рабочий день


async def test_share_link_is_null_without_bot_username(miniapp_api, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")
    await add_user(SENDER)
    task_id = await add_task(SENDER, date(2026, 10, 5))
    body = (await share(miniapp_api, "task", task_id)).json()
    assert body["link"] is None and body["code"]


def test_task_title_fits_task_limit():
    assert task_title("  Короткое  ") == "Короткое"
    long = "Уведомление об исчисленных суммах налогов, авансовых платежей по налогам, сборов"
    cut = task_title(long)
    assert len(cut) == 60 and cut.endswith("…") and long.startswith(cut[:-1])


# --- Приглашение по коду ------------------------------------------------------------------------


async def test_get_invite_has_no_sender_data(miniapp_api):
    await add_user(SENDER)
    task_id = await add_task(SENDER, date(2026, 10, 5))
    code = (await share(miniapp_api, "task", task_id)).json()["code"]

    r = await miniapp_api.request("GET", f"/api/shares/{code}", NEWCOMER)
    assert r.status_code == 200
    assert r.json() == {
        "code": code,
        "item_type": "task",
        "title": "Оплатить аренду",
        "due_date": "2026-10-05",
    }
    assert [(e.user_id, e.props) for e in await events("share_opened")] == [
        (NEWCOMER, {"code_valid": True})
    ]


@pytest.mark.parametrize("code", ["abcdefghijkl", "bad", "has%20space"])
async def test_unknown_code_is_404(miniapp_api, code):
    r = await miniapp_api.request("GET", f"/api/shares/{code}", RECIPIENT)
    assert r.status_code == 404
    r = await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)
    assert r.status_code == 404
    assert [e.props for e in await events("share_opened")] == [{"code_valid": False}]
    assert await task_count(RECIPIENT) == 0


# --- Принятие -----------------------------------------------------------------------------------


async def test_accept_creates_task_with_default_reminder(miniapp_api):
    """Получатель без профиля и без строки User: задача создаётся, напоминание за день в 10:00."""
    await add_user(SENDER)
    uo_id = await add_obligation(SENDER, date(2026, 10, 28))
    code = (await share(miniapp_api, "obligation", uo_id)).json()["code"]

    r = await miniapp_api.request("POST", f"/api/shares/{code}/accept", NEWCOMER)
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    task = body["task"]
    assert (task["type"], task["title"], task["due_date"], task["status"]) == (
        "task",
        "Тестовое годовое обязательство",
        "2026-10-28",
        "upcoming",
    )
    assert (task["remind_offset_days"], task["remind_hour"], task["remind_minute"]) == (1, 10, 0)
    # 27.10 10:00 по Москве (пояс по умолчанию) = 07:00 UTC
    assert await task_notifications(task["id"]) == [
        ("task", "pending", datetime(2026, 10, 27, 7, tzinfo=UTC))
    ]
    assert [(e.user_id, e.props) for e in await events("share_accepted")] == [
        (NEWCOMER, {"item_type": "obligation", "backdated": False})
    ]
    assert [(e.user_id, e.props) for e in await events("task_created")] == [
        (
            NEWCOMER,
            {"source": "share", "remind_offset": 1, "backdated": False, "done_at_create": False},
        )
    ]


async def test_accept_is_idempotent(miniapp_api):
    await add_user(SENDER)
    await add_user(RECIPIENT)
    task_id = await add_task(SENDER, date(2026, 10, 5))
    code = (await share(miniapp_api, "task", task_id)).json()["code"]

    first = (await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)).json()
    second = (await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)).json()
    assert first["created"] is True and second["created"] is False
    assert first["task"]["id"] == second["task"]["id"]
    assert await task_count(RECIPIENT) == 1
    assert len(await events("share_accepted")) == 1
    assert len(await events("task_created")) == 1
    assert len(await task_notifications(first["task"]["id"])) == 1


async def test_accept_again_after_delete_creates_new_task(miniapp_api):
    await add_user(SENDER)
    await add_user(RECIPIENT)
    task_id = await add_task(SENDER, date(2026, 10, 5))
    code = (await share(miniapp_api, "task", task_id)).json()["code"]
    first = (await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)).json()
    await miniapp_api.request("DELETE", f"/api/tasks/{first['task']['id']}", RECIPIENT)

    again = (await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)).json()
    assert again["created"] is True and again["task"]["id"] != first["task"]["id"]
    async with SessionLocal() as s:
        accepts = list(await s.scalars(select(SharedItemAccept)))
    assert [(a.user_id, a.task_id) for a in accepts] == [(RECIPIENT, again["task"]["id"])]


async def test_accept_does_not_touch_sender_item(miniapp_api):
    await add_user(SENDER)
    await add_user(RECIPIENT)
    task_id = await add_task(SENDER, date(2026, 10, 5))
    code = (await share(miniapp_api, "task", task_id)).json()["code"]
    await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)
    assert await task_count(SENDER) == 1
    # Отправитель по-прежнему видит свою задачу; получатель — свою, у каждого своя.
    r = await miniapp_api.request("GET", f"/api/items/task/{task_id}", RECIPIENT)
    assert r.status_code == 404


async def test_accept_past_date_is_overdue_without_reminder(miniapp_api):
    """D32: дата в прошлом допустима — задача просроченная, напоминаний нет."""
    await add_user(SENDER)
    await add_user(RECIPIENT)
    task_id = await add_task(SENDER, date(2026, 9, 15))
    code = (await share(miniapp_api, "task", task_id)).json()["code"]

    body = (await miniapp_api.request("POST", f"/api/shares/{code}/accept", RECIPIENT)).json()
    assert body["task"]["status"] == "overdue"
    assert body["task"]["due_date"] == "2026-09-15"
    assert await task_notifications(body["task"]["id"]) == []
    assert [e.props for e in await events("share_accepted")] == [
        {"item_type": "task", "backdated": True}
    ]
    assert [e.props["backdated"] for e in await events("task_created")] == [True]
