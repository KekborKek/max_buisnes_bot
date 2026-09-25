"""T10 (#51): календарь, карточка, отметка и «Неверный срок» — API мини-приложения.

Справочник — ТЕСТОВЫЕ ДАННЫЕ из backend/tests/fixtures/ (фикстура test_reference).
«Сейчас» — 23.09.2026 10:00 по Москве (фикстура miniapp_api), если тест не переставил часы.
"""

import logging
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.calendar.reminders import sync_obligation_notifications, sync_task_notification
from app.core.db import SessionLocal
from app.core.models import (
    DialogState,
    Event,
    Notification,
    Profile,
    Task,
    User,
    UserObligation,
    WrongDateReport,
)

OWNER = 301
STRANGER = 302
NOW = datetime(2026, 9, 23, 7, tzinfo=UTC)


async def add_user(user_id: int, *, tz: str = "Europe/Moscow", complete: bool = True) -> None:
    async with SessionLocal() as s:
        s.add(User(user_id=user_id))
        await s.flush()  # внешний ключ Profile → User
        s.add(
            Profile(
                user_id=user_id,
                timezone=tz,
                income_band="lt10" if complete else None,
                regime="usn6" if complete else None,
                has_employees=False if complete else None,
                nds_payer=False if complete else None,
            )
        )
        await s.commit()


async def add_obligation(
    user_id: int,
    due: date,
    *,
    obligation_id: str = "test_yearly",
    original: date | None = None,
    done_at: datetime | None = None,
    tz: str = "Europe/Moscow",
    now: datetime = NOW,
) -> int:
    """UserObligation + уведомления по плану на момент `now`, как после сборки календаря."""
    async with SessionLocal() as s:
        uo = UserObligation(
            user_id=user_id,
            obligation_id=obligation_id,
            rule_version=1,
            original_date=original or due,
            due_date=due,
            done_at=done_at,
        )
        s.add(uo)
        await s.flush()
        await sync_obligation_notifications(
            s, uo, needs_prep=obligation_id == "test_yearly", tz=tz, settings=None, now=now
        )
        await s.commit()
        return uo.id


async def add_task(
    user_id: int,
    due: date,
    *,
    title: str = "Оплатить аренду",
    done_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> int:
    async with SessionLocal() as s:
        task = Task(
            user_id=user_id, title=title, due_date=due, done_at=done_at, deleted_at=deleted_at
        )
        s.add(task)
        await s.flush()
        await sync_task_notification(s, task, tz="Europe/Moscow", now=NOW)
        await s.commit()
        return task.id


async def notifications(item_type: str, item_id: int) -> list[Notification]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification)
            .where(Notification.item_type == item_type, Notification.item_id == item_id)
            .order_by(Notification.id)
        )
        return list(rows)


async def pending_kinds(item_type: str, item_id: int) -> list[str]:
    return sorted(n.kind for n in await notifications(item_type, item_id) if n.status == "pending")


async def events(name: str) -> list[Event]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Event).where(Event.name == name).order_by(Event.id))
        return list(rows)


# --- Доступ: 401 без initData, 404 на чужое ---------------------------------------------------

ALL_ENDPOINTS = [
    ("GET", "/api/me", None),
    ("GET", "/api/calendar?from=2026-09-01&to=2026-12-31", None),
    ("GET", "/api/items/obligation/1", None),
    ("GET", "/api/items/task/1", None),
    ("POST", "/api/items/obligation/1/done", None),
    ("DELETE", "/api/items/obligation/1/done", None),
    ("POST", "/api/items/task/1/done", None),
    ("DELETE", "/api/items/task/1/done", None),
    ("POST", "/api/obligations/1/report", None),
    ("POST", "/api/tasks", {"title": "Аренда", "due_date": "2026-10-05"}),
    ("PATCH", "/api/tasks/1", {"title": "Аренда"}),
    ("DELETE", "/api/tasks/1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ALL_ENDPOINTS)
async def test_every_endpoint_requires_init_data(miniapp_api, method, path, body):
    r = await miniapp_api.request(method, path, None, json=body)
    assert r.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ALL_ENDPOINTS)
async def test_every_endpoint_rejects_forged_init_data(miniapp_api, method, path, body):
    r = await miniapp_api.client.request(
        method, path, json=body, headers={"X-Max-Init-Data": "user=%7B%7D&hash=forged"}
    )
    assert r.status_code == 401


FOREIGN_ENDPOINTS = [
    ("GET", "/api/items/{type}/{id}", None),
    ("POST", "/api/items/{type}/{id}/done", None),
    ("DELETE", "/api/items/{type}/{id}/done", None),
]


@pytest.mark.parametrize("item_type", ["obligation", "task"])
@pytest.mark.parametrize(("method", "path", "body"), FOREIGN_ENDPOINTS)
async def test_foreign_item_is_404(miniapp_api, item_type, method, path, body):
    await add_user(OWNER)
    await add_user(STRANGER)
    if item_type == "obligation":
        item_id = await add_obligation(OWNER, date(2026, 12, 28))
    else:
        item_id = await add_task(OWNER, date(2026, 10, 5))
    url = path.format(type=item_type, id=item_id)

    r = await miniapp_api.request(method, url, STRANGER, json=body)
    assert r.status_code == 404
    missing = path.format(type=item_type, id=item_id + 1000)
    assert (await miniapp_api.request(method, missing, OWNER, json=body)).status_code == 404
    # чужая попытка ничего не поменяла
    r = await miniapp_api.request("GET", f"/api/items/{item_type}/{item_id}", OWNER)
    assert r.json()["done_at"] is None
    assert not await events("item_done")
    assert not await events("item_undone")


async def test_foreign_obligation_report_is_404(miniapp_api):
    await add_user(OWNER)
    await add_user(STRANGER)
    uo_id = await add_obligation(OWNER, date(2026, 12, 28))
    r = await miniapp_api.request("POST", f"/api/obligations/{uo_id}/report", STRANGER)
    assert r.status_code == 404
    async with SessionLocal() as s:
        assert (await s.scalars(select(WrongDateReport))).first() is None


async def test_unknown_item_type_is_rejected(miniapp_api):
    await add_user(OWNER)
    r = await miniapp_api.request("GET", "/api/items/event/1", OWNER)
    assert r.status_code == 422


async def test_obligation_missing_from_catalog_is_404(miniapp_api):
    """Запись убрали из справочника: карточки нет, в списке события нет."""
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 10, 1), obligation_id="removed_record")
    r = await miniapp_api.request("GET", f"/api/items/obligation/{uo_id}", OWNER)
    assert r.status_code == 404
    r = await miniapp_api.request("GET", "/api/calendar?from=2026-09-01&to=2026-12-31", OWNER)
    assert r.json() == []


# --- /api/me ------------------------------------------------------------------------------


async def test_me_without_profile(miniapp_api):
    r = await miniapp_api.request("GET", "/api/me", OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == OWNER
    assert body["has_profile"] is False
    assert body["profile"] is None
    assert body["draft"] is None


async def test_me_with_unfinished_onboarding_has_no_profile(miniapp_api):
    """Профиль создан на /start, ответов ещё нет (D24) — для мини-аппа профиля нет."""
    await add_user(OWNER, complete=False)
    body = (await miniapp_api.request("GET", "/api/me", OWNER)).json()
    assert body["has_profile"] is False
    assert body["profile"] is None


async def test_me_with_profile(miniapp_api):
    await add_user(OWNER, tz="Asia/Vladivostok")
    async with SessionLocal() as s:
        profile = await s.get(Profile, OWNER)
        profile.nds_payer = None  # D25: определить нельзя
        profile.calendar_built_at = datetime(2026, 9, 20, 7, tzinfo=UTC)
        await s.commit()
    body = (await miniapp_api.request("GET", "/api/me", OWNER)).json()
    assert body["has_profile"] is True
    assert body["profile"] == {
        "income_band": "lt10",
        "regime": "usn6",
        "has_employees": False,
        "timezone": "Asia/Vladivostok",
        "nds_payer": None,
        "calendar_built_at": "2026-09-20T07:00:00Z",
        "reference_checked_at": "2026-01-01",  # version тестового справочника
        # T14-13a (#85): настройки напоминаний экрана 13, у нового профиля — умолчания
        "reminders": {"d30": True, "d7": True, "hour": 10, "digest": True},
    }


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"task_draft": {"title": " Оплатить аренду ", "due_date": "2026-11-05"}},
            {"title": "Оплатить аренду", "due_date": "2026-11-05"},
        ),
        ({}, None),
        ({"task_draft": None}, None),
        ({"task_draft": {"title": "Без даты"}}, None),
        ({"task_draft": {"title": "Аренда", "due_date": "5 ноября"}}, None),
        ({"task_draft": "Аренда 5 ноября"}, None),
    ],
)
async def test_me_draft_from_dialog_state(miniapp_api, data, expected):
    await add_user(OWNER)
    async with SessionLocal() as s:
        s.add(DialogState(user_id=OWNER, state="task_confirm", data=data))
        await s.commit()
    body = (await miniapp_api.request("GET", "/api/me", OWNER)).json()
    assert body["draft"] == expected


# --- GET /api/calendar ------------------------------------------------------------------------


async def test_calendar_period_plus_all_overdue(miniapp_api):
    await add_user(OWNER)
    await add_user(STRANGER)
    overdue = await add_obligation(OWNER, date(2026, 3, 25), obligation_id="test_quarterly")
    await add_obligation(
        OWNER,
        date(2026, 6, 25),
        obligation_id="test_quarterly",
        done_at=datetime(2026, 6, 20, tzinfo=UTC),
    )  # прошедшее отмеченное вне периода — не нужно
    in_period = await add_obligation(
        OWNER, date(2026, 10, 26), original=date(2026, 10, 25), obligation_id="test_quarterly"
    )
    await add_obligation(OWNER, date(2027, 1, 25), obligation_id="test_quarterly")  # вне периода
    task = await add_task(OWNER, date(2026, 10, 5))
    old_task = await add_task(OWNER, date(2026, 9, 1))  # просроченная задача
    await add_task(OWNER, date(2026, 10, 6), deleted_at=NOW)  # удалённая
    await add_task(STRANGER, date(2026, 10, 7))  # чужая
    today_done = await add_obligation(
        OWNER, date(2026, 9, 23), obligation_id="test_quarterly", done_at=NOW
    )

    r = await miniapp_api.request("GET", "/api/calendar?from=2026-09-23&to=2026-12-31", OWNER)
    assert r.status_code == 200
    got = [(i["type"], i["id"], i["status"]) for i in r.json()]
    assert got == [
        ("obligation", overdue, "overdue"),
        ("task", old_task, "overdue"),
        ("obligation", today_done, "done"),
        ("task", task, "upcoming"),
        ("obligation", in_period, "upcoming"),
    ]
    item = next(i for i in r.json() if i["id"] == in_period and i["type"] == "obligation")
    assert item == {
        "type": "obligation",
        "id": in_period,
        "title": "Тестовое квартальное обязательство",
        "category": "reports",
        "due_date": "2026-10-26",
        "original_date": "2026-10-25",
        "status": "upcoming",
        "done_at": None,
    }
    task_item = next(i for i in r.json() if i["type"] == "task" and i["id"] == task)
    assert task_item["category"] == "custom"
    assert task_item["original_date"] == task_item["due_date"] == "2026-10-05"


async def test_calendar_empty_for_new_user(miniapp_api):
    r = await miniapp_api.request("GET", "/api/calendar?from=2026-09-01&to=2026-12-31", OWNER)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.parametrize(
    "query", ["from=2026-12-31&to=2026-09-01", "from=2026-09-01", "from=вчера&to=2026-12-31"]
)
async def test_calendar_bad_period_is_422(miniapp_api, query):
    await add_user(OWNER)
    r = await miniapp_api.request("GET", f"/api/calendar?{query}", OWNER)
    assert r.status_code == 422


async def test_calendar_status_in_user_timezone(miniapp_api):
    """27.10 14:00 UTC: во Владивостоке уже 28-е — срок сегодня; в Москве — ещё завтра."""
    vlad, msk = 401, 402
    await add_user(vlad, tz="Asia/Vladivostok")
    await add_user(msk, tz="Europe/Moscow")
    await add_obligation(vlad, date(2026, 10, 28), obligation_id="test_quarterly")
    await add_obligation(msk, date(2026, 10, 28), obligation_id="test_quarterly")
    miniapp_api.now = datetime(2026, 10, 27, 14, tzinfo=UTC)
    q = "/api/calendar?from=2026-10-01&to=2026-10-31"
    assert [i["status"] for i in (await miniapp_api.request("GET", q, vlad)).json()] == ["today"]
    assert [i["status"] for i in (await miniapp_api.request("GET", q, msk)).json()] == ["upcoming"]


# --- GET /api/items -----------------------------------------------------------------------------


async def test_obligation_card(miniapp_api, caplog):
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 12, 28))
    with caplog.at_level(logging.WARNING, logger="app.api.service"):
        r = await miniapp_api.request("GET", f"/api/items/obligation/{uo_id}", OWNER)
    assert r.status_code == 200
    assert r.json() == {
        "type": "obligation",
        "id": uo_id,
        "title": "Тестовое годовое обязательство",
        "category": "taxes",
        "due_date": "2026-12-28",
        "original_date": "2026-12-28",
        "status": "upcoming",
        "done_at": None,
        "norm": "Тестовая норма №1",
        "source_url": "https://example.invalid/test-yearly",
        "howto_steps": [
            "Тестовый шаг 1.",
            "Тестовый шаг 2 — до {notice_date}.",  # правила нет (D28) — как есть
            "Тестовый шаг 3 — до 28 декабря.",
        ],
        "howto_link": {"label": "Открыть тест", "url": "https://example.invalid/howto"},
        "penalty_text": "Тестовый текст о пене.",
        "last_checked_at": "2026-01-01",
        "remind_offset_days": None,
        "remind_hour": None,
    }
    assert "test_yearly" in caplog.text
    assert "{notice_date}" in caplog.text


async def test_obligation_card_due_date_next_year_has_year(miniapp_api):
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2027, 12, 28))
    r = await miniapp_api.request("GET", f"/api/items/obligation/{uo_id}", OWNER)
    assert r.json()["howto_steps"][2] == "Тестовый шаг 3 — до 28 декабря 2027."


async def test_task_card(miniapp_api):
    await add_user(OWNER)
    task_id = await add_task(OWNER, date(2026, 11, 5))
    r = await miniapp_api.request("GET", f"/api/items/task/{task_id}", OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "custom"
    assert body["status"] == "upcoming"
    assert body["remind_offset_days"] == 1
    assert body["remind_hour"] == 10
    for field in ("norm", "source_url", "howto_steps", "howto_link", "penalty_text"):
        assert body[field] is None


# --- Отметка и снятие -------------------------------------------------------------------------


async def test_mark_done_is_idempotent_and_cancels_notifications(miniapp_api):
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 12, 28))
    assert await pending_kinds("obligation", uo_id) == ["d1", "d30", "d7", "overdue"]
    url = f"/api/items/obligation/{uo_id}/done"

    first = await miniapp_api.request("POST", url, OWNER)
    second = await miniapp_api.request("POST", url, OWNER)

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "done"
    assert first.json()["done_at"] is not None
    assert second.json()["done_at"] == first.json()["done_at"]
    assert first.json()["howto_steps"]  # в ответе полная карточка
    assert await pending_kinds("obligation", uo_id) == []
    done = await events("item_done")
    assert len(done) == 1
    assert done[0].user_id == OWNER
    assert done[0].props == {
        "item_id": uo_id,
        "item_type": "obligation",
        "source": "card",
        "days_before_deadline": 96,
    }


async def test_mark_overdue_gives_negative_days(miniapp_api):
    """D19: просроченное отметить можно, days_before_deadline отрицательный."""
    await add_user(OWNER)
    uo_id = await add_obligation(
        OWNER,
        date(2026, 9, 20),
        obligation_id="test_quarterly",
        now=datetime(2026, 9, 20, 7, tzinfo=UTC),  # d1 уже ушёл бы, overdue ещё впереди
    )
    assert await pending_kinds("obligation", uo_id) == ["overdue"]
    r = await miniapp_api.request("POST", f"/api/items/obligation/{uo_id}/done", OWNER)
    assert r.json()["status"] == "done"
    assert (await events("item_done"))[0].props["days_before_deadline"] == -3
    assert await pending_kinds("obligation", uo_id) == []


async def test_unmark_restores_future_notifications(miniapp_api):
    """Правило 1: отметка отменяет d1 и overdue, снятие возвращает те, что ещё в будущем."""
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 10, 26), obligation_id="test_quarterly")
    assert await pending_kinds("obligation", uo_id) == ["d1", "d7", "overdue"]
    url = f"/api/items/obligation/{uo_id}/done"
    await miniapp_api.request("POST", url, OWNER)
    assert await pending_kinds("obligation", uo_id) == []

    # d7 (19.10 10:00) к моменту снятия уже в прошлом — возвращаются только d1 и overdue
    miniapp_api.now = datetime(2026, 10, 20, 7, tzinfo=UTC)
    r1 = await miniapp_api.request("DELETE", url, OWNER)
    r2 = await miniapp_api.request("DELETE", url, OWNER)

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["status"] == "upcoming"
    assert r1.json()["done_at"] is None
    assert await pending_kinds("obligation", uo_id) == ["d1", "overdue"]
    undone = await events("item_undone")
    assert len(undone) == 1
    assert undone[0].props == {"item_id": uo_id, "item_type": "obligation", "source": "card"}


async def test_unmark_not_done_changes_nothing(miniapp_api):
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 12, 28))
    before = [(n.id, n.status) for n in await notifications("obligation", uo_id)]
    r = await miniapp_api.request("DELETE", f"/api/items/obligation/{uo_id}/done", OWNER)
    assert r.status_code == 200
    assert [(n.id, n.status) for n in await notifications("obligation", uo_id)] == before
    assert not await events("item_undone")


async def test_task_mark_and_unmark(miniapp_api):
    await add_user(OWNER)
    task_id = await add_task(OWNER, date(2026, 10, 5))
    assert await pending_kinds("task", task_id) == ["task"]
    url = f"/api/items/task/{task_id}/done"

    r = await miniapp_api.request("POST", url, OWNER)
    assert r.json()["status"] == "done"
    assert await pending_kinds("task", task_id) == []
    assert (await events("item_done"))[0].props == {
        "item_id": task_id,
        "item_type": "task",
        "source": "card",
        "days_before_deadline": 12,
    }

    r = await miniapp_api.request("DELETE", url, OWNER)
    assert r.json()["status"] == "upcoming"
    assert await pending_kinds("task", task_id) == ["task"]


async def test_mark_done_visible_in_calendar(miniapp_api):
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 10, 26), obligation_id="test_quarterly")
    await miniapp_api.request("POST", f"/api/items/obligation/{uo_id}/done", OWNER)
    r = await miniapp_api.request("GET", "/api/calendar?from=2026-09-23&to=2026-12-31", OWNER)
    assert [i["status"] for i in r.json()] == ["done"]


# --- «Неверный срок» ------------------------------------------------------------------------


async def test_report_wrong_date_once(miniapp_api):
    await add_user(OWNER)
    uo_id = await add_obligation(OWNER, date(2026, 12, 28))
    url = f"/api/obligations/{uo_id}/report"
    r1 = await miniapp_api.request("POST", url, OWNER)
    r2 = await miniapp_api.request("POST", url, OWNER)
    assert r1.status_code == r2.status_code == 204
    assert r1.content == b""
    async with SessionLocal() as s:
        reports = list(await s.scalars(select(WrongDateReport)))
    assert [(r.user_id, r.obligation_id, r.due_date) for r in reports] == [
        (OWNER, "test_yearly", date(2026, 12, 28))
    ]
    reported = await events("wrong_date_reported")
    assert len(reported) == 1
    assert reported[0].props == {"item_id": uo_id, "item_type": "obligation", "source": "card"}


async def test_report_is_for_obligations_only(miniapp_api):
    """id задачи в /obligations/{id}/report — это id обязательства, которого нет."""
    await add_user(OWNER)
    task_id = await add_task(OWNER, date(2026, 10, 5))
    r = await miniapp_api.request("POST", f"/api/obligations/{task_id}/report", OWNER)
    assert r.status_code == 404
