"""T10 (#51): свои задачи из мини-аппа — экраны 16 и 17, D11; задачи задним числом — D32 (#108).

«Сейчас» — 23.09.2026 10:00 по Москве (фикстура miniapp_api), если тест не переставил часы.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Event, Notification, Profile, Task, User

OWNER = 501
STRANGER = 502


async def add_user(user_id: int, *, tz: str = "Europe/Moscow") -> None:
    async with SessionLocal() as s:
        s.add(User(user_id=user_id))
        await s.flush()  # внешний ключ Profile → User
        s.add(
            Profile(
                user_id=user_id,
                timezone=tz,
                income_band="lt10",
                regime="usn6",
                has_employees=False,
                nds_payer=False,
            )
        )
        await s.commit()


async def task_notifications(task_id: int) -> list[tuple[str, str, datetime]]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification)
            .where(Notification.item_type == "task", Notification.item_id == task_id)
            .order_by(Notification.id)
        )
        return [(n.kind, n.status, n.send_at.replace(tzinfo=UTC)) for n in rows]


async def events(name: str) -> list[Event]:
    async with SessionLocal() as s:
        return list(await s.scalars(select(Event).where(Event.name == name).order_by(Event.id)))


async def create(api, user_id=OWNER, **body):
    payload = {"title": "Оплатить аренду", "due_date": "2026-10-05", **body}
    return await api.request("POST", "/api/tasks", user_id, json=payload)


# --- POST /api/tasks ------------------------------------------------------------------------


async def test_create_task_with_defaults(miniapp_api):
    """Название + «Сохранить», остальное по умолчанию: за 1 день в 10:00 (экран 17)."""
    await add_user(OWNER)
    r = await create(miniapp_api)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "type": "task",
        "id": body["id"],
        "title": "Оплатить аренду",
        "category": "custom",
        "due_date": "2026-10-05",
        "original_date": "2026-10-05",
        "status": "upcoming",
        "done_at": None,
        "norm": None,
        "source_url": None,
        "howto_steps": None,
        "howto_link": None,
        "penalty_text": None,
        "last_checked_at": None,
        "remind_offset_days": 1,
        "remind_hour": 10,
        "remind_minute": 0,
    }
    # 04.10 10:00 по Москве = 07:00 UTC
    assert await task_notifications(body["id"]) == [
        ("task", "pending", datetime(2026, 10, 4, 7, tzinfo=UTC))
    ]
    created = await events("task_created")
    assert [(e.user_id, e.props) for e in created] == [
        (
            OWNER,
            {"source": "form", "remind_offset": 1, "backdated": False, "done_at_create": False},
        )
    ]


async def test_create_task_reminder_in_user_timezone(miniapp_api):
    await add_user(OWNER, tz="Asia/Vladivostok")
    r = await create(miniapp_api, remind_offset_days=7, remind_hour=18, due_date="2026-11-05")
    assert r.status_code == 200
    # 29.10 18:00 во Владивостоке (UTC+10) = 08:00 UTC
    assert await task_notifications(r.json()["id"]) == [
        ("task", "pending", datetime(2026, 10, 29, 8, tzinfo=UTC))
    ]
    assert (await events("task_created"))[0].props == {
        "source": "form",
        "remind_offset": 7,
        "backdated": False,
        "done_at_create": False,
    }


async def test_create_task_today_uses_user_timezone(miniapp_api):
    """23.09 20:00 UTC: во Владивостоке уже 24-е — 23-е в прошлом (D32), 24-е — сегодня."""
    await add_user(OWNER, tz="Asia/Vladivostok")
    miniapp_api.now = datetime(2026, 9, 23, 20, tzinfo=UTC)
    past = await create(miniapp_api, due_date="2026-09-23")
    assert past.status_code == 200
    assert past.json()["status"] == "overdue"
    r = await create(miniapp_api, due_date="2026-09-24", remind_offset_days=0)
    assert r.status_code == 200
    assert r.json()["status"] == "today"
    assert [e.props["backdated"] for e in await events("task_created")] == [True, False]


async def test_create_task_for_today_without_future_reminder(miniapp_api):
    """Напоминание «за 1 день» уже в прошлом — задача есть, уведомления нет."""
    await add_user(OWNER)
    r = await create(miniapp_api, due_date="2026-09-23")
    assert r.status_code == 200
    assert r.json()["status"] == "today"
    assert await task_notifications(r.json()["id"]) == []


async def test_create_task_creates_user_row(miniapp_api):
    """Мини-апп открыли раньше, чем бот увидел человека: User создаётся, Profile — нет."""
    r = await create(miniapp_api, user_id=OWNER)
    assert r.status_code == 200
    async with SessionLocal() as s:
        assert await s.get(User, OWNER) is not None
        assert await s.get(Profile, OWNER) is None
    # пояс по умолчанию — Москва
    assert await task_notifications(r.json()["id"]) == [
        ("task", "pending", datetime(2026, 10, 4, 7, tzinfo=UTC))
    ]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "   "}, "title"),
        ({"title": "я" * 61}, "title"),
        ({"due_date": "5 ноября"}, "due_date"),
        ({"remind_offset_days": 2}, "remind_offset_days"),
        ({"remind_hour": 24}, "remind_hour"),
        ({"remind_hour": -1}, "remind_hour"),
        ({"remind_minute": 60}, "remind_minute"),
        ({"remind_minute": -1}, "remind_minute"),
    ],
)
async def test_create_task_validation(miniapp_api, body, field):
    await add_user(OWNER)
    r = await create(miniapp_api, **body)
    assert r.status_code == 422
    assert [e["loc"] for e in r.json()["detail"]] == [["body", field]]
    async with SessionLocal() as s:
        assert (await s.scalars(select(Task))).first() is None
    assert not await events("task_created")


async def test_create_task_title_limits(miniapp_api):
    await add_user(OWNER)
    r = await create(miniapp_api, title="  " + "я" * 60 + "  ")
    assert r.status_code == 200
    assert r.json()["title"] == "я" * 60


@pytest.mark.parametrize("offset", [0, 1, 3, 7])
@pytest.mark.parametrize(("hour", "minute"), [(0, 0), (9, 5), (10, 0), (18, 30), (23, 59)])
async def test_create_task_allowed_options(miniapp_api, offset, hour, minute):
    await add_user(OWNER)
    r = await create(
        miniapp_api,
        due_date="2026-10-30",
        remind_offset_days=offset,
        remind_hour=hour,
        remind_minute=minute,
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["remind_offset_days"], body["remind_hour"], body["remind_minute"]) == (
        offset,
        hour,
        minute,
    )


async def test_create_task_reminder_at_any_minute(miniapp_api):
    """#103: 07:45 по Москве = 04:45 UTC; карточка отдаёт то же время."""
    await add_user(OWNER)
    r = await create(miniapp_api, remind_hour=7, remind_minute=45)
    assert r.status_code == 200
    task_id = r.json()["id"]
    # 04.10 07:45 по Москве = 04:45 UTC
    assert await task_notifications(task_id) == [
        ("task", "pending", datetime(2026, 10, 4, 4, 45, tzinfo=UTC))
    ]
    card = (await miniapp_api.request("GET", f"/api/items/task/{task_id}", OWNER)).json()
    assert (card["remind_hour"], card["remind_minute"]) == (7, 45)


# --- Задачи задним числом (D32, #108) ----------------------------------------------------


async def all_notifications() -> int:
    async with SessionLocal() as s:
        return len(list(await s.scalars(select(Notification))))


@pytest.mark.parametrize("offset", [0, 1, 3, 7])
@pytest.mark.parametrize(("hour", "minute"), [(0, 0), (10, 0), (23, 59)])
async def test_backdated_task_is_overdue_without_notifications(miniapp_api, offset, hour, minute):
    """Вчерашняя дата: задача создаётся просроченной, ни одной строки Notification."""
    await add_user(OWNER)
    r = await create(
        miniapp_api,
        due_date="2026-09-22",
        remind_offset_days=offset,
        remind_hour=hour,
        remind_minute=minute,
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["done_at"]) == ("overdue", None)
    assert await all_notifications() == 0
    assert [e.props for e in await events("task_created")] == [
        {"source": "form", "remind_offset": offset, "backdated": True, "done_at_create": False}
    ]


async def test_backdated_task_created_done(miniapp_api):
    """«Уже выполнено»: done_at = сейчас, статус done, уведомлений нет — и после снятия отметки."""
    await add_user(OWNER)
    r = await create(miniapp_api, due_date="2026-09-01", done=True)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["done_at"].startswith("2026-09-23T07:00")
    assert await all_notifications() == 0
    assert [e.props for e in await events("task_created")] == [
        {"source": "form", "remind_offset": 1, "backdated": True, "done_at_create": True}
    ]
    # в календаре выполненной
    items = (
        await miniapp_api.request("GET", "/api/calendar?from=2026-09-01&to=2026-09-30", OWNER)
    ).json()
    assert [(i["id"], i["status"]) for i in items if i["type"] == "task"] == [(body["id"], "done")]

    r = await miniapp_api.request("DELETE", f"/api/items/task/{body['id']}/done", OWNER)
    assert r.status_code == 200
    assert r.json()["status"] == "overdue"
    assert await all_notifications() == 0


async def test_backdated_task_can_be_marked_from_card(miniapp_api):
    """Без «Уже выполнено» — просроченная; «Отметить выполненным» из карточки работает."""
    await add_user(OWNER)
    task_id = (await create(miniapp_api, due_date="2026-09-20")).json()["id"]
    r = await miniapp_api.request("POST", f"/api/items/task/{task_id}/done", OWNER)
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert await all_notifications() == 0


async def test_done_flag_accepted_for_future_date(miniapp_api):
    """done принимается на любую дату (решение по #108): отметка сразу, напоминания нет."""
    await add_user(OWNER)
    r = await create(miniapp_api, done=True)
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert await task_notifications(r.json()["id"]) == []
    assert (await events("task_created"))[0].props["done_at_create"] is True


async def test_patch_to_past_date_cancels_notification(miniapp_api):
    """Перенос на прошедший день: прежнее напоминание cancelled, нового нет."""
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request(
        "PATCH", f"/api/tasks/{task_id}", OWNER, json={"due_date": "2026-09-21"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "overdue"
    assert await task_notifications(task_id) == [
        ("task", "cancelled", datetime(2026, 10, 4, 7, tzinfo=UTC))
    ]


# --- PATCH /api/tasks/{id} ----------------------------------------------------------------


async def test_patch_task_reschedules_notification(miniapp_api):
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request(
        "PATCH",
        f"/api/tasks/{task_id}",
        OWNER,
        json={"due_date": "2026-10-10", "remind_offset_days": 3, "remind_hour": 9},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["title"], body["due_date"], body["remind_offset_days"], body["remind_hour"]) == (
        "Оплатить аренду",
        "2026-10-10",
        3,
        9,
    )
    # старое → cancelled, новое: 07.10 09:00 по Москве = 06:00 UTC
    assert await task_notifications(task_id) == [
        ("task", "cancelled", datetime(2026, 10, 4, 7, tzinfo=UTC)),
        ("task", "pending", datetime(2026, 10, 7, 6, tzinfo=UTC)),
    ]
    assert len(await events("task_created")) == 1


async def test_patch_minute_only_reschedules_notification(miniapp_api):
    """#103: новое время (только минуты) пересоздаёт напоминание."""
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request(
        "PATCH", f"/api/tasks/{task_id}", OWNER, json={"remind_minute": 15}
    )
    assert r.status_code == 200
    assert (r.json()["remind_hour"], r.json()["remind_minute"]) == (10, 15)
    assert await task_notifications(task_id) == [
        ("task", "cancelled", datetime(2026, 10, 4, 7, tzinfo=UTC)),
        ("task", "pending", datetime(2026, 10, 4, 7, 15, tzinfo=UTC)),
    ]


async def test_patch_title_only_keeps_notification(miniapp_api):
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request("PATCH", f"/api/tasks/{task_id}", OWNER, json={"title": "Аренда"})
    assert r.status_code == 200
    assert r.json()["title"] == "Аренда"
    assert await task_notifications(task_id) == [
        ("task", "pending", datetime(2026, 10, 4, 7, tzinfo=UTC))
    ]


async def test_patch_overdue_task_title_keeps_past_date(miniapp_api):
    """Прежнюю дату в прошлом оставить можно, новую в прошлом — тоже (D32), без напоминания."""
    await add_user(OWNER)
    task_id = (await create(miniapp_api, due_date="2026-09-25")).json()["id"]
    miniapp_api.now = datetime(2026, 9, 28, 7, tzinfo=UTC)
    url = f"/api/tasks/{task_id}"

    r = await miniapp_api.request(
        "PATCH", url, OWNER, json={"title": "Аренда", "due_date": "2026-09-25"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "overdue"

    r = await miniapp_api.request("PATCH", url, OWNER, json={"due_date": "2026-09-27"})
    assert r.status_code == 200
    assert r.json()["status"] == "overdue"

    r = await miniapp_api.request("PATCH", url, OWNER, json={"due_date": "2026-09-30"})
    assert r.status_code == 200
    assert r.json()["status"] == "upcoming"


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "я" * 61},
        {"remind_offset_days": 5},
        {"remind_hour": 24},
        {"remind_minute": 60},
    ],
)
async def test_patch_task_validation(miniapp_api, body):
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request("PATCH", f"/api/tasks/{task_id}", OWNER, json=body)
    assert r.status_code == 422
    card = (await miniapp_api.request("GET", f"/api/items/task/{task_id}", OWNER)).json()
    assert (
        card["title"],
        card["remind_offset_days"],
        card["remind_hour"],
        card["remind_minute"],
    ) == ("Оплатить аренду", 1, 10, 0)


async def test_patch_foreign_or_deleted_task_is_404(miniapp_api):
    await add_user(OWNER)
    await add_user(STRANGER)
    task_id = (await create(miniapp_api)).json()["id"]
    url = f"/api/tasks/{task_id}"
    r = await miniapp_api.request("PATCH", url, STRANGER, json={"title": "Чужое"})
    assert r.status_code == 404
    await miniapp_api.request("DELETE", url, OWNER)
    r = await miniapp_api.request("PATCH", url, OWNER, json={"title": "После удаления"})
    assert r.status_code == 404


async def test_patch_done_task_keeps_notifications_cancelled(miniapp_api):
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    await miniapp_api.request("POST", f"/api/items/task/{task_id}/done", OWNER)
    r = await miniapp_api.request(
        "PATCH", f"/api/tasks/{task_id}", OWNER, json={"due_date": "2026-10-20"}
    )
    assert r.json()["status"] == "done"
    assert [status for _, status, _ in await task_notifications(task_id)] == ["cancelled"]


# --- DELETE /api/tasks/{id} ---------------------------------------------------------------


async def test_delete_task_is_soft_and_idempotent(miniapp_api):
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    url = f"/api/tasks/{task_id}"

    r1 = await miniapp_api.request("DELETE", url, OWNER)
    r2 = await miniapp_api.request("DELETE", url, OWNER)

    assert r1.status_code == r2.status_code == 204
    assert r1.content == b""
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        assert task is not None
        assert task.deleted_at is not None
    assert [status for _, status, _ in await task_notifications(task_id)] == ["cancelled"]
    # старая ссылка на удалённую задачу — 404 (экран 16, notFound)
    assert (
        await miniapp_api.request("GET", f"/api/items/task/{task_id}", OWNER)
    ).status_code == 404
    cal = await miniapp_api.request("GET", "/api/calendar?from=2026-09-01&to=2026-12-31", OWNER)
    assert cal.json() == []


async def test_delete_foreign_task_is_404(miniapp_api):
    await add_user(OWNER)
    await add_user(STRANGER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request("DELETE", f"/api/tasks/{task_id}", STRANGER)
    assert r.status_code == 404
    assert (await miniapp_api.request("DELETE", "/api/tasks/99999", OWNER)).status_code == 404
    async with SessionLocal() as s:
        assert (await s.get(Task, task_id)).deleted_at is None
