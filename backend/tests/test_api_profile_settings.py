"""T14-13a (#85): экран 13 — настройки напоминаний в мини-аппе и пересчёт уведомлений.

`PUT /api/profile/settings` и `profile.reminders` в `/api/me` (docs/screens/should-12-13-19.md,
docs/spec/reminders.md «Когда создаются уведомления»). Справочник — ТЕСТОВЫЕ ДАННЫЕ из
backend/tests/fixtures/: test_yearly (needs_prep) и test_quarterly (без подготовки).
«Сейчас» — 23.09.2026 10:00 по Москве (07:00 UTC, фикстура miniapp_api), если тест не переставил.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.api import deps
from app.core.db import SessionLocal
from app.core.models import Event, Notification, Profile, Task, User
from app.main import app

from .test_api_calendar import add_obligation, add_task

OWNER = 321
PATH = "/api/profile/settings"
MSK = "Europe/Moscow"
VLAT = "Asia/Vladivostok"
DEFAULTS = {"d30": True, "d7": True, "hour": 10, "digest": True}


def body(**changes) -> dict:
    return {**DEFAULTS, "timezone": MSK, **changes}


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


async def add_profile(
    user_id: int = OWNER, *, complete: bool = True, reminders: dict | None = None, tz: str = MSK
) -> None:
    async with SessionLocal() as s:
        s.add(User(user_id=user_id))
        await s.flush()
        profile = Profile(
            user_id=user_id,
            timezone=tz,
            income_band="lt10" if complete else None,
            regime="usn6" if complete else None,
            has_employees=False if complete else None,
            nds_payer=False if complete else None,
        )
        if reminders is not None:
            profile.reminders = reminders
        s.add(profile)
        await s.commit()


async def stored_profile(user_id: int = OWNER) -> Profile:
    async with SessionLocal() as s:
        return await s.get(Profile, user_id)


async def all_notifications() -> list[tuple[int, str, str, int, str, datetime]]:
    """(id, item_type, kind, item_id, status, send_at) — снимок «ничего не изменилось»."""
    async with SessionLocal() as s:
        rows = await s.scalars(select(Notification).order_by(Notification.id))
        return [
            (n.id, n.item_type, n.kind, n.item_id, n.status, n.send_at.replace(tzinfo=UTC))
            for n in rows
        ]


async def pending(item_type: str, item_id: int) -> dict[str, datetime]:
    """kind → send_at (UTC) у pending-уведомлений события."""
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification).where(
                Notification.item_type == item_type,
                Notification.item_id == item_id,
                Notification.status == "pending",
            )
        )
        found: dict[str, datetime] = {}
        for n in rows:
            assert n.kind not in found, f"дубль pending {n.kind}"
            found[n.kind] = n.send_at.replace(tzinfo=UTC)
        return found


async def statuses(item_type: str, item_id: int) -> dict[str, list[str]]:
    async with SessionLocal() as s:
        rows = await s.scalars(
            select(Notification)
            .where(Notification.item_type == item_type, Notification.item_id == item_id)
            .order_by(Notification.id)
        )
        found: dict[str, list[str]] = {}
        for n in rows:
            found.setdefault(n.kind, []).append(n.status)
        return found


async def add_notification(item_type: str, item_id: int, kind: str, send_at: datetime, **kw) -> int:
    async with SessionLocal() as s:
        n = Notification(
            user_id=OWNER,
            item_type=item_type,
            item_id=item_id,
            kind=kind,
            send_at=send_at,
            status=kw.get("status", "pending"),
            attempts=kw.get("attempts", 0),
        )
        s.add(n)
        await s.commit()
        return n.id


async def events(name: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Event).where(Event.name == name).order_by(Event.id))
        return [e.props for e in rows]


# --- /api/me отдаёт profile.reminders --------------------------------------------------------


async def test_me_without_settings_gives_defaults(miniapp_api):
    await add_profile(reminders={})
    r = await miniapp_api.request("GET", "/api/me", OWNER)
    assert r.status_code == 200
    assert r.json()["profile"]["reminders"] == DEFAULTS


async def test_me_gives_saved_settings_including_digest(miniapp_api):
    await add_profile(reminders={"d30": False, "hour": 18, "digest": False, "extra": 1})
    r = await miniapp_api.request("GET", "/api/me", OWNER)
    assert r.json()["profile"]["reminders"] == {
        "d30": False,
        "d7": True,
        "hour": 18,
        "digest": False,
    }


# --- Коды ответа ----------------------------------------------------------------------------


async def test_without_init_data_is_401(miniapp_api):
    await add_profile()
    assert (await miniapp_api.request("PUT", PATH, None, json=body())).status_code == 401


@pytest.mark.parametrize("complete", [False, None])
async def test_incomplete_profile_is_409(miniapp_api, complete):
    if complete is False:
        await add_profile(complete=False)
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(hour=18))
    assert r.status_code == 409
    assert r.json()["detail"] == "profile_incomplete"
    assert await events("reminder_settings_changed") == []


@pytest.mark.parametrize(
    "bad",
    [
        {"hour": 11},
        {"hour": 0},
        {"timezone": "Europe/London"},
        {"timezone": "UTC+3"},
        {"d30": None},
    ],
)
async def test_invalid_body_is_422(miniapp_api, bad):
    await add_profile()
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(**bad))
    assert r.status_code == 422
    assert (await stored_profile()).timezone == MSK


@pytest.mark.parametrize("missing", ["d30", "d7", "hour", "digest", "timezone"])
async def test_every_field_is_required(miniapp_api, missing):
    await add_profile()
    payload = body()
    del payload[missing]
    assert (await miniapp_api.request("PUT", PATH, OWNER, json=payload)).status_code == 422


async def test_ok_returns_updated_profile(miniapp_api):
    await add_profile()
    r = await miniapp_api.request(
        "PUT", PATH, OWNER, json=body(d7=False, hour=9, digest=False, timezone=VLAT)
    )
    assert r.status_code == 200
    out = r.json()
    assert out["timezone"] == VLAT
    assert out["reminders"] == {"d30": True, "d7": False, "hour": 9, "digest": False}
    assert out["reference_checked_at"] == "2026-01-01"
    # /api/me видит то же
    me = (await miniapp_api.request("GET", "/api/me", OWNER)).json()
    assert me["profile"]["reminders"] == out["reminders"]
    assert me["profile"]["timezone"] == VLAT


async def test_rebuild_response_has_reminders(miniapp_api):
    await add_profile(reminders={"digest": False})
    r = await miniapp_api.request("POST", "/api/calendar/rebuild", OWNER)
    assert r.status_code == 200
    assert r.json()["profile"]["reminders"] == {**DEFAULTS, "digest": False}


# --- Запись в Profile.reminders ---------------------------------------------------------------


async def test_unknown_keys_are_kept(miniapp_api):
    """digest=false и чужой ключ до сохранения — после сохранения на месте."""
    await add_profile(reminders={"digest": False, "extra": "x", "hour": 10})
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(d30=False, digest=False))
    assert r.status_code == 200
    assert (await stored_profile()).reminders == {
        "digest": False,
        "extra": "x",
        "d30": False,
        "d7": True,
        "hour": 10,
    }
    assert r.json()["reminders"]["digest"] is False


async def test_digest_turned_back_on(miniapp_api):
    """Сводку, выключенную кнопкой «Без сводки» (экран 12), возвращают здесь."""
    await add_profile(reminders={"d30": True, "d7": True, "hour": 10, "digest": False})
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(digest=True))
    assert r.json()["reminders"]["digest"] is True
    assert (await stored_profile()).reminders["digest"] is True
    assert await events("reminder_settings_changed") == [{"changed": ["digest"]}]


# --- Пересчёт уведомлений --------------------------------------------------------------------


async def test_d30_off_cancels_future_d30_only(miniapp_api):
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 12, 28))  # test_yearly, needs_prep
    snooze = await add_notification("obligation", uo, "snooze", utc(2026, 9, 24, 7))
    assert set(await pending("obligation", uo)) == {"d30", "d7", "d1", "overdue", "snooze"}

    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(d30=False))
    assert r.status_code == 200

    assert set(await pending("obligation", uo)) == {"d7", "d1", "overdue", "snooze"}
    assert (await statuses("obligation", uo))["d30"] == ["cancelled"]
    async with SessionLocal() as s:
        assert (await s.get(Notification, snooze)).send_at.replace(tzinfo=UTC) == utc(
            2026, 9, 24, 7
        )
    assert await events("reminder_settings_changed") == [{"changed": ["d30"]}]


async def test_d7_off_then_on_returns_it(miniapp_api):
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 10, 26), obligation_id="test_quarterly")
    await miniapp_api.request("PUT", PATH, OWNER, json=body(d7=False))
    assert set(await pending("obligation", uo)) == {"d1", "overdue"}
    await miniapp_api.request("PUT", PATH, OWNER, json=body(d7=True))
    assert await pending("obligation", uo) == {
        "d7": utc(2026, 10, 19, 7),
        "d1": utc(2026, 10, 25, 7),
        "overdue": utc(2026, 10, 27, 7),
    }


async def test_d1_cannot_be_turned_off(miniapp_api):
    """d1 в теле нет; лишнее поле d1=false игнорируется — d1 остаётся."""
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 12, 28))
    r = await miniapp_api.request(
        "PUT", PATH, OWNER, json={**body(d30=False, d7=False), "d1": False}
    )
    assert r.status_code == 200
    assert "d1" not in r.json()["reminders"]
    assert set(await pending("obligation", uo)) == {"d1", "overdue"}
    assert "d1" not in (await stored_profile()).reminders


async def test_hour_change_moves_send_at(miniapp_api):
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 12, 28))
    task = await add_task(OWNER, date(2026, 10, 5))  # своё время задачи — 10:00, не меняется

    await miniapp_api.request("PUT", PATH, OWNER, json=body(hour=18))

    assert await pending("obligation", uo) == {
        "d30": utc(2026, 11, 28, 15),
        "d7": utc(2026, 12, 21, 15),
        "d1": utc(2026, 12, 27, 15),
        "overdue": utc(2026, 12, 29, 15),
    }
    assert await pending("task", task) == {"task": utc(2026, 10, 4, 7)}


async def test_timezone_vladivostok_moves_obligations_and_tasks(miniapp_api):
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 12, 28))
    task = await add_task(OWNER, date(2026, 10, 5))
    async with SessionLocal() as s:  # задача со своим временем: за 3 дня в 18:00
        other = Task(
            user_id=OWNER,
            title="Сдать отчёт",
            due_date=date(2026, 11, 10),
            remind_offset_days=3,
            remind_hour=18,
        )
        s.add(other)
        await s.flush()
        other_id = other.id
        await s.commit()
    await add_notification("task", other_id, "task", utc(2026, 11, 7, 15))  # 18:00 MSK
    before = len(await all_notifications())

    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(timezone=VLAT))
    assert r.status_code == 200

    # 10:00 во Владивостоке (UTC+10) = 00:00 UTC того же дня
    assert await pending("obligation", uo) == {
        "d30": utc(2026, 11, 28, 0),
        "d7": utc(2026, 12, 21, 0),
        "d1": utc(2026, 12, 27, 0),
        "overdue": utc(2026, 12, 29, 0),
    }
    assert await pending("task", task) == {"task": utc(2026, 10, 4, 0)}
    assert await pending("task", other_id) == {"task": utc(2026, 11, 7, 8)}
    # send_at сдвинут на месте: новых строк и отменённых копий нет
    assert len(await all_notifications()) == before
    assert (await stored_profile()).timezone == VLAT
    assert await events("reminder_settings_changed") == [{"changed": ["timezone"]}]


async def test_done_items_get_no_new_notifications(miniapp_api):
    await add_profile()
    done_uo = await add_obligation(OWNER, date(2026, 12, 28), done_at=utc(2026, 9, 20, 7))
    done_task = await add_task(OWNER, date(2026, 10, 5), done_at=utc(2026, 9, 20, 7))
    deleted_task = await add_task(OWNER, date(2026, 10, 6), deleted_at=utc(2026, 9, 20, 7))

    await miniapp_api.request("PUT", PATH, OWNER, json=body(hour=18, timezone=VLAT))

    assert await pending("obligation", done_uo) == {}
    assert await pending("task", done_task) == {}
    assert await pending("task", deleted_task) == {}


async def test_task_notifications_in_past_or_in_flight_untouched(miniapp_api):
    await add_profile()
    task = await add_task(OWNER, date(2026, 10, 5))
    async with SessionLocal() as s:  # старое pending задачи уже «в прошлом» — ждёт отправки
        n = (await s.scalars(select(Notification).where(Notification.item_id == task))).one()
        n.send_at = utc(2026, 9, 23, 6)
        await s.commit()
    flying_task = await add_task(OWNER, date(2026, 10, 7))
    async with SessionLocal() as s:  # «аренда» планировщика: attempts=1, send_at = now + 1 ч
        n = (await s.scalars(select(Notification).where(Notification.item_id == flying_task))).one()
        n.attempts = 1
        n.send_at = utc(2026, 9, 23, 8)
        await s.commit()

    await miniapp_api.request("PUT", PATH, OWNER, json=body(timezone=VLAT))

    assert await pending("task", task) == {"task": utc(2026, 9, 23, 6)}
    assert await pending("task", flying_task) == {"task": utc(2026, 9, 23, 8)}


async def test_new_time_already_passed_keeps_old_pending(miniapp_api):
    """Решение по #85: Москва → Владивосток в день d1, когда 10:00 там уже прошло.

    d1 («последняя защита») и напоминание задачи остаются со старым send_at, а не отменяются.
    """
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 12, 28))
    task = await add_task(OWNER, date(2026, 12, 28))  # за 1 день, 10:00 МСК = 27.12 07:00 UTC
    async with SessionLocal() as s:  # d30 и d7 к этому дню уже ушли
        for n in await s.scalars(
            select(Notification).where(
                Notification.item_id == uo, Notification.kind.in_(("d30", "d7"))
            )
        ):
            n.status = "sent"
        await s.commit()
    miniapp_api.now = utc(2026, 12, 27, 5)  # 08:00 МСК, 15:00 во Владивостоке

    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(timezone=VLAT))
    assert r.status_code == 200

    assert await pending("obligation", uo) == {
        "d1": utc(2026, 12, 27, 7),  # старое время, не отменено
        "overdue": utc(2026, 12, 29, 0),  # будущее — по новому поясу
    }
    assert await pending("task", task) == {"task": utc(2026, 12, 27, 7)}


async def test_obligation_missing_from_catalog_is_skipped(miniapp_api):
    await add_profile()
    uo = await add_obligation(OWNER, date(2026, 10, 1), obligation_id="removed_record")
    before = await pending("obligation", uo)
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(timezone=VLAT))
    assert r.status_code == 200
    assert await pending("obligation", uo) == before


# --- Идемпотентность и событие ---------------------------------------------------------------


async def test_repeat_same_body_changes_nothing(miniapp_api):
    await add_profile()
    await add_obligation(OWNER, date(2026, 12, 28))
    await add_obligation(OWNER, date(2026, 10, 26), obligation_id="test_quarterly")
    await add_task(OWNER, date(2026, 10, 5))
    payload = body(d30=False, hour=18, timezone=VLAT)

    first = await miniapp_api.request("PUT", PATH, OWNER, json=payload)
    snapshot = await all_notifications()
    second = await miniapp_api.request("PUT", PATH, OWNER, json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert await all_notifications() == snapshot
    assert await events("reminder_settings_changed") == [{"changed": ["d30", "hour", "timezone"]}]


async def test_defaults_body_on_fresh_profile_is_noop(miniapp_api):
    await add_profile(reminders={})
    uo = await add_obligation(OWNER, date(2026, 12, 28))
    before = await all_notifications()
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body())
    assert r.status_code == 200
    assert await all_notifications() == before
    assert (await stored_profile()).reminders == {}
    assert await events("reminder_settings_changed") == []
    assert set(await pending("obligation", uo)) == {"d30", "d7", "d1", "overdue"}


# --- Справочник недоступен -------------------------------------------------------------------


async def test_reference_unavailable_is_503_and_nothing_saved(miniapp_api):
    await add_profile()
    await add_obligation(OWNER, date(2026, 12, 28))
    before = await all_notifications()
    app.dependency_overrides[deps.optional_reference] = lambda: None

    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(hour=18, timezone=VLAT))

    assert r.status_code == 503
    assert r.json()["detail"] == "reference_unavailable"
    profile = await stored_profile()
    assert profile.timezone == MSK
    assert profile.reminders == {"d30": True, "d7": True, "hour": 10}  # умолчания модели
    assert await all_notifications() == before
    assert await events("reminder_settings_changed") == []
    assert await events("error") == [{"where": "settings", "kind": "reference_file"}]


async def test_digest_only_change_works_without_reference(miniapp_api):
    """От сводки send_at не зависят — пересчёт не нужен, справочник тоже."""
    await add_profile()
    app.dependency_overrides[deps.optional_reference] = lambda: None
    r = await miniapp_api.request("PUT", PATH, OWNER, json=body(digest=False))
    assert r.status_code == 200
    assert r.json()["reminders"]["digest"] is False
    assert r.json()["reference_checked_at"] is None
