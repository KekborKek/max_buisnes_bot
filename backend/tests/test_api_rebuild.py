"""T14-19 (#78): «Пересобрать» на экране 19 — POST /api/calendar/rebuild — и дата сверки в /api/me.

После пересборки не сбрасываются: отметки по прошедшим событиям, свои задачи, часовой пояс,
настройки уведомлений (docs/screens/should-12-13-19.md, экран 19). Повторный вызов идемпотентен.

Справочник — ТЕСТОВЫЕ ДАННЫЕ из backend/tests/fixtures/ (version "2026-01-01"): test_yearly
(28 декабря, УСН, needs_prep) и test_quarterly (25-е после квартала, всем).
«Сейчас» — 23.09.2026 10:00 по Москве (фикстура miniapp_api), если тест не переставил часы.
"""

import dataclasses
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api import deps, service
from app.api import profile as profile_api
from app.calendar.build import build_calendar
from app.calendar.reminders import as_utc, cancel_pending
from app.calendar.types import ReferenceFileError
from app.core.db import SessionLocal
from app.core.models import Event, Notification, Profile, Task, User, UserObligation
from app.main import app

from .test_api_calendar import add_obligation, add_task, pending_kinds

OWNER = 311
NOW = datetime(2026, 9, 23, 7, tzinfo=UTC)
TZ = "Asia/Vladivostok"
REMINDERS = {"d30": False, "d7": True, "hour": 18}
PATH = "/api/calendar/rebuild"


async def add_profile(user_id: int = OWNER, *, complete: bool = True, regime: str = "usn6") -> None:
    """Профиль после онбординга: свой пояс и свои настройки уведомлений, как после экрана 13."""
    async with SessionLocal() as s:
        s.add(User(user_id=user_id))
        await s.flush()
        s.add(
            Profile(
                user_id=user_id,
                timezone=TZ,
                income_band="lt10" if complete else None,
                regime=regime if complete else None,
                has_employees=False if complete else None,
                nds_payer=False if complete else None,
                started_at=datetime(2026, 9, 23, 6, 58, tzinfo=UTC),
                reminders=dict(REMINDERS),
            )
        )
        await s.commit()


async def mark_done(uo_id: int, at: datetime) -> None:
    """Отметка, как её ставит карточка 16: done_at и отмена pending-уведомлений."""
    async with SessionLocal() as s:
        uo = await s.get(UserObligation, uo_id)
        uo.done_at = at
        await cancel_pending(s, "obligation", uo_id)
        await s.commit()


async def obligations(user_id: int = OWNER) -> list[UserObligation]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(UserObligation)
            .where(UserObligation.user_id == user_id)
            .order_by(UserObligation.due_date, UserObligation.obligation_id)
        )
        return list(rows)


async def pending_notifications() -> list[tuple[str, int, str, datetime]]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification)
            .where(Notification.user_id == OWNER, Notification.status == "pending")
            .order_by(Notification.id)
        )
        return [(n.item_type, n.item_id, n.kind, n.send_at) for n in rows]


async def event_props(name: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Event).where(Event.user_id == OWNER, Event.name == name).order_by(Event.id)
        )
        return [e.props for e in rows]


# --- Доступ --------------------------------------------------------------------------------


async def test_rebuild_requires_init_data(miniapp_api):
    await add_profile()
    r = await miniapp_api.request("POST", PATH, None)
    assert r.status_code == 401
    assert await obligations() == []


async def test_rebuild_without_answers_is_409(miniapp_api):
    """Онбординг не пройден (D24) — собирать не из чего, в БД ничего не пишем."""
    await add_profile(complete=False)
    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 409
    assert r.json()["detail"] == "profile_incomplete"
    assert await obligations() == []
    assert await event_props("calendar_built") == []


async def test_rebuild_without_profile_is_409(miniapp_api):
    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 409


# --- Пересборка ------------------------------------------------------------------------------


async def test_first_build_from_miniapp(miniapp_api):
    """Профиль есть, календаря ещё нет — эндпоинт собирает его, rebuild=false."""
    await add_profile()
    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 200
    body = r.json()
    # test_quarterly 25.10, test_yearly 28.12 — до конца 2026 года
    assert body["items_count"] == 2
    assert body["nearest_due_date"] == "2026-10-25"
    assert body["profile"]["timezone"] == TZ
    assert body["profile"]["calendar_built_at"] == "2026-09-23T07:00:00Z"
    assert body["profile"]["reference_checked_at"] == "2026-01-01"
    assert await event_props("calendar_built") == [
        {"items_count": 2, "seconds_since_start": 120, "rebuild": False}
    ]


async def test_rebuild_keeps_marks_tasks_timezone_and_settings(miniapp_api):
    await add_profile()
    assert (await miniapp_api.request("POST", PATH, OWNER)).status_code == 200

    # Отметка по прошедшему событию и по будущему, просроченное без отметки, своя задача.
    past_done = await add_obligation(
        OWNER,
        date(2026, 7, 27),
        obligation_id="test_quarterly",
        original=date(2026, 7, 25),
        tz=TZ,
    )
    await mark_done(past_done, datetime(2026, 7, 20, 5, tzinfo=UTC))
    past_overdue = await add_obligation(
        OWNER,
        date(2026, 4, 27),
        obligation_id="test_quarterly",
        original=date(2026, 4, 25),
        tz=TZ,
    )
    [future_q] = [
        uo.id
        for uo in await obligations()
        if uo.obligation_id == "test_quarterly" and uo.original_date == date(2026, 10, 25)
    ]
    await mark_done(future_q, datetime(2026, 9, 22, 5, tzinfo=UTC))
    task_id = await add_task(OWNER, date(2026, 10, 5), title="Оплатить аренду")
    task_pending = await pending_kinds("task", task_id)
    assert task_pending == ["task"]

    miniapp_api.now = datetime(2026, 9, 24, 7, tzinfo=UTC)
    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 200

    rows = {uo.id: uo for uo in await obligations()}
    # отметки на месте
    assert rows[past_done].done_at is not None
    assert rows[future_q].done_at is not None
    assert await pending_kinds("obligation", future_q) == []
    # просроченное без отметки остаётся просроченным
    assert past_overdue in rows and rows[past_overdue].done_at is None
    # своя задача не тронута, её напоминание живо
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        assert (task.title, task.due_date, task.deleted_at) == (
            "Оплатить аренду",
            date(2026, 10, 5),
            None,
        )
        profile = await s.get(Profile, OWNER)
        # пояс и настройки уведомлений не сброшены
        assert profile.timezone == TZ
        assert profile.reminders == REMINDERS
        assert as_utc(profile.calendar_built_at) == datetime(2026, 9, 24, 7, tzinfo=UTC)
    assert await pending_kinds("task", task_id) == task_pending
    # настройки применены: d30 выключен, время — 18:00 по Владивостоку (08:00 UTC)
    [yearly] = [
        uo
        for uo in rows.values()
        if uo.obligation_id == "test_yearly" and uo.due_date == date(2026, 12, 28)
    ]
    assert await pending_kinds("obligation", yearly.id) == ["d1", "d7", "overdue"]
    async with SessionLocal() as s:
        send_at = await s.scalar(
            select(Notification.send_at).where(
                Notification.item_type == "obligation",
                Notification.item_id == yearly.id,
                Notification.kind == "d1",
            )
        )
    assert as_utc(send_at) == datetime(2026, 12, 27, 8, tzinfo=UTC)
    assert [p["rebuild"] for p in await event_props("calendar_built")] == [False, True]


async def test_rebuild_after_profile_change_keeps_past_marks(miniapp_api):
    """Исправили режим в боте («Изменить») и пересобрали: отметка по прошедшему остаётся,
    неотмеченное, что больше не подходит профилю, уходит."""
    await add_profile()
    assert (await miniapp_api.request("POST", PATH, OWNER)).status_code == 200
    old_yearly = await add_obligation(
        OWNER,
        date(2025, 12, 29),
        obligation_id="test_yearly",
        original=date(2025, 12, 28),
        tz=TZ,
    )
    await mark_done(old_yearly, datetime(2025, 12, 20, 5, tzinfo=UTC))
    async with SessionLocal() as s:
        profile = await s.get(Profile, OWNER)
        profile.regime = "patent"  # test_yearly — только для УСН
        await s.commit()

    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 200
    rows = await obligations()
    assert [
        (uo.obligation_id, uo.due_date) for uo in rows if uo.obligation_id == "test_yearly"
    ] == [("test_yearly", date(2025, 12, 29))]
    assert next(uo for uo in rows if uo.id == old_yearly).done_at is not None


async def test_rebuild_twice_is_idempotent(miniapp_api):
    await add_profile()
    first = await miniapp_api.request("POST", PATH, OWNER)
    before = [(uo.id, uo.obligation_id, uo.due_date) for uo in await obligations()]
    pending_before = await pending_notifications()

    second = await miniapp_api.request("POST", PATH, OWNER)
    third = await miniapp_api.request("POST", PATH, OWNER)

    assert first.status_code == second.status_code == third.status_code == 200
    assert second.json()["items_count"] == third.json()["items_count"] == 2
    assert [(uo.id, uo.obligation_id, uo.due_date) for uo in await obligations()] == before
    assert await pending_notifications() == pending_before
    async with SessionLocal() as s:
        total = await s.scalar(
            select(func.count()).select_from(Notification).where(Notification.user_id == OWNER)
        )
    assert total == len(pending_before)  # ни одного cancelled-дубля
    assert [p["rebuild"] for p in await event_props("calendar_built")] == [False, True, True]


async def test_rebuild_without_workdays_year_is_503(miniapp_api):
    """Сборка до 31.12.2028, а в тестовом производственном календаре только 2026–2027."""
    await add_profile()
    miniapp_api.now = datetime(2027, 6, 1, 7, tzinfo=UTC)
    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 503
    assert r.json()["detail"] == "reference_unavailable"
    assert await obligations() == []
    assert await event_props("error") == [{"where": "rebuild", "kind": "missing_year"}]
    assert await event_props("calendar_built") == []


async def test_rebuild_with_broken_reference_file_is_503(miniapp_api, monkeypatch):
    """Файл справочника не загрузился (ReferenceFileError) — 503 и событие, как у бота, не 500.

    Настоящая optional_reference с «битым» get_reference, без подмены из фикстуры.
    """
    await add_profile()

    def broken():
        raise ReferenceFileError("нет файла obligations.yaml")

    monkeypatch.setattr(deps, "get_reference", broken)
    app.dependency_overrides.pop(deps.optional_reference, None)
    r = await miniapp_api.request("POST", PATH, OWNER)
    assert r.status_code == 503
    assert r.json()["detail"] == "reference_unavailable"
    assert await obligations() == []
    assert await event_props("error") == [{"where": "rebuild", "kind": "reference_file"}]
    assert await event_props("calendar_built") == []


async def test_race_on_unique_key_retries_once(miniapp_api, monkeypatch):
    """Соседний запрос успел собрать календарь, наш упёрся в уникальный ключ: откат, повтор —
    200 без дублей, одно calendar_built с rebuild из первой попытки (для нас — первая сборка)."""
    await add_profile()
    calls = []

    async def racing_build(session, user_id, *, now, reference):
        calls.append(now)
        if len(calls) == 1:
            # «Соседний запрос»: своя сессия, своя сборка, коммит раньше нас.
            async with SessionLocal() as other:
                await build_calendar(other, user_id, now=now, reference=reference)
                await other.commit()
            raise IntegrityError("INSERT INTO user_obligations", {}, Exception("UNIQUE"))
        return await build_calendar(session, user_id, now=now, reference=reference)

    monkeypatch.setattr(profile_api, "build_calendar", racing_build)
    r = await miniapp_api.request("POST", PATH, OWNER)

    assert r.status_code == 200
    assert len(calls) == 2
    assert r.json()["items_count"] == 2
    keys = [(uo.obligation_id, uo.due_date) for uo in await obligations()]
    assert len(keys) == len(set(keys)) == 7  # как у обычной сборки до 31.12.2027, без дублей
    assert await event_props("calendar_built") == [
        {"items_count": 2, "seconds_since_start": 120, "rebuild": False}
    ]


# --- Дата сверки справочника в /api/me ------------------------------------------------------


async def test_me_without_reference_still_answers(miniapp_api):
    """Справочник недоступен — /api/me не падает (это вход в мини-апп), даты сверки нет."""
    await add_profile()
    app.dependency_overrides[deps.optional_reference] = lambda: None
    r = await miniapp_api.request("GET", "/api/me", OWNER)
    assert r.status_code == 200
    assert r.json()["profile"]["reference_checked_at"] is None


def test_optional_reference_swallows_broken_files(monkeypatch):
    def broken():
        raise ReferenceFileError("нет файла")

    monkeypatch.setattr(deps, "get_reference", broken)
    assert deps.optional_reference() is None


def test_reference_checked_at_needs_iso_version(test_reference):
    odd = dataclasses.replace(
        test_reference, catalog=dataclasses.replace(test_reference.catalog, version="v7")
    )
    assert service.reference_checked_at(odd) is None
    assert service.reference_checked_at(test_reference) == date(2026, 1, 1)
    assert service.reference_checked_at(None) is None
