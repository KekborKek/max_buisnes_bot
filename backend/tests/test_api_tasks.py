"""T10 (#51): свои задачи из мини-аппа — экраны 16 и 17, D11.

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
    }
    # 04.10 10:00 по Москве = 07:00 UTC
    assert await task_notifications(body["id"]) == [
        ("task", "pending", datetime(2026, 10, 4, 7, tzinfo=UTC))
    ]
    created = await events("task_created")
    assert [(e.user_id, e.props) for e in created] == [
        (OWNER, {"source": "form", "remind_offset": 1})
    ]


async def test_create_task_reminder_in_user_timezone(miniapp_api):
    await add_user(OWNER, tz="Asia/Vladivostok")
    r = await create(miniapp_api, remind_offset_days=7, remind_hour=18, due_date="2026-11-05")
    assert r.status_code == 200
    # 29.10 18:00 во Владивостоке (UTC+10) = 08:00 UTC
    assert await task_notifications(r.json()["id"]) == [
        ("task", "pending", datetime(2026, 10, 29, 8, tzinfo=UTC))
    ]
    assert (await events("task_created"))[0].props == {"source": "form", "remind_offset": 7}


async def test_create_task_today_uses_user_timezone(miniapp_api):
    """23.09 20:00 UTC: во Владивостоке уже 24-е — 23-е в прошлом, 24-е можно."""
    await add_user(OWNER, tz="Asia/Vladivostok")
    miniapp_api.now = datetime(2026, 9, 23, 20, tzinfo=UTC)
    assert (await create(miniapp_api, due_date="2026-09-23")).status_code == 422
    r = await create(miniapp_api, due_date="2026-09-24", remind_offset_days=0)
    assert r.status_code == 200
    assert r.json()["status"] == "today"


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
        ({"due_date": "2026-09-22"}, "due_date"),
        ({"due_date": "5 ноября"}, "due_date"),
        ({"remind_offset_days": 2}, "remind_offset_days"),
        ({"remind_hour": 11}, "remind_hour"),
        ({"remind_hour": 0}, "remind_hour"),
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
@pytest.mark.parametrize("hour", [9, 10, 18])
async def test_create_task_allowed_options(miniapp_api, offset, hour):
    await add_user(OWNER)
    r = await create(
        miniapp_api, due_date="2026-10-30", remind_offset_days=offset, remind_hour=hour
    )
    assert r.status_code == 200
    assert (r.json()["remind_offset_days"], r.json()["remind_hour"]) == (offset, hour)


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
    """Прежнюю дату в прошлом оставить можно, новую в прошлом — нет."""
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
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "due_date"]

    r = await miniapp_api.request("PATCH", url, OWNER, json={"due_date": "2026-09-30"})
    assert r.status_code == 200
    assert r.json()["status"] == "upcoming"


@pytest.mark.parametrize(
    "body", [{"title": ""}, {"title": "я" * 61}, {"remind_offset_days": 5}, {"remind_hour": 12}]
)
async def test_patch_task_validation(miniapp_api, body):
    await add_user(OWNER)
    task_id = (await create(miniapp_api)).json()["id"]
    r = await miniapp_api.request("PATCH", f"/api/tasks/{task_id}", OWNER, json=body)
    assert r.status_code == 422
    card = (await miniapp_api.request("GET", f"/api/items/task/{task_id}", OWNER)).json()
    assert (card["title"], card["remind_offset_days"], card["remind_hour"]) == (
        "Оплатить аренду",
        1,
        10,
    )


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
