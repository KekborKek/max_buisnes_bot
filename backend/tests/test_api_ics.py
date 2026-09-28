"""Лента iCalendar «Добавить в календарь телефона» (экран 19): формат RFC 5545 и доступ по токену.

Справочник — ТЕСТОВЫЕ ДАННЫЕ из backend/tests/fixtures/ (фикстура test_reference).
«Сейчас» — 23.09.2026 10:00 по Москве (фикстура miniapp_api).
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.api import ics
from app.api.ics_format import FeedEvent, escape_text, fold, format_trigger, render_calendar
from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.models import Event, Profile, Task, User, UserObligation
from app.main import app

OWNER = 701
STRANGER = 702
SECRET = "ics-test-secret"
BASE = "https://vse-uspel.ru"
NOW = datetime(2026, 9, 23, 7, tzinfo=UTC)


@pytest.fixture
def ics_settings(miniapp_api):
    s = Settings(_env_file=None, max_webhook_secret=SECRET, public_base_url=BASE)
    app.dependency_overrides[ics.current_settings] = lambda: s
    try:
        yield s
    finally:
        app.dependency_overrides.pop(ics.current_settings, None)


async def add_user(user_id: int, *, hour: int = 10) -> None:
    async with SessionLocal() as s:
        s.add(User(user_id=user_id))
        await s.flush()
        s.add(
            Profile(
                user_id=user_id,
                timezone="Europe/Moscow",
                income_band="lt10",
                regime="usn6",
                has_employees=False,
                nds_payer=False,
                reminders={"d30": True, "d7": True, "hour": hour},
            )
        )
        await s.commit()


async def add_obligation(user_id: int, due: date, *, done: bool = False) -> int:
    async with SessionLocal() as s:
        uo = UserObligation(
            user_id=user_id,
            obligation_id="test_yearly",
            rule_version=1,
            original_date=due,
            due_date=due,
            done_at=NOW if done else None,
        )
        s.add(uo)
        await s.commit()
        return uo.id


async def add_task(user_id: int, title: str, due: date, **extra) -> int:
    async with SessionLocal() as s:
        task = Task(user_id=user_id, title=title, due_date=due, **extra)
        s.add(task)
        await s.commit()
        return task.id


def unfold(body: str) -> list[str]:
    """Логические строки ленты: продолжения (CRLF + пробел) склеены."""
    assert body.endswith("\r\n")
    return body[:-2].replace("\r\n ", "").split("\r\n")


def events_of(lines: list[str]) -> list[list[str]]:
    """Свойства каждого VEVENT; строки VALARM — с префиксом «VALARM/»."""
    out: list[list[str]] = []
    alarm = False
    for line in lines:
        if line == "BEGIN:VEVENT":
            out.append([])
        elif line in ("BEGIN:VALARM", "END:VALARM"):
            alarm = line == "BEGIN:VALARM"
        elif line != "END:VEVENT" and out and not line.startswith("END:VCALENDAR"):
            out[-1].append(f"VALARM/{line}" if alarm else line)
    return out


def prop(event: list[str], name: str) -> str:
    values = [ln.split(":", 1)[1] for ln in event if ln.split(":", 1)[0] == name]
    assert len(values) == 1, (name, event)
    return values[0]


async def feed(api, user_id: int, secret: str = SECRET):
    return await api.client.get(f"/api/ics/{ics.make_token(user_id, secret)}.ics")


# --- Формат (без БД) --------------------------------------------------------------------------


def test_escape_text():
    assert escape_text("a\\b;c,d\ne\r\nf") == "a\\\\b\\;c\\,d\\ne\\nf"


def test_fold_cyrillic_by_octets_without_splitting_chars():
    line = "SUMMARY:" + "Ж" * 100  # 8 + 200 октетов
    folded = fold(line)
    parts = folded.split("\r\n")
    assert len(parts) > 1
    for i, part in enumerate(parts):
        raw = part.encode("utf-8")
        assert len(raw) <= 75
        if i:
            assert part.startswith(" ")
        raw.decode("utf-8")  # ни один символ не разрезан
    assert folded.replace("\r\n ", "") == line
    # первая строка заполнена до предела: 8 ASCII + 33 × 2 октета = 74, 34-й символ не влез
    assert len(parts[0].encode()) == 74


def test_fold_short_line_untouched():
    assert fold("VERSION:2.0") == "VERSION:2.0"


def test_format_trigger():
    assert format_trigger(24 * 60 - 10 * 60) == "-PT14H"
    assert format_trigger(24 * 60 - (18 * 60 + 30)) == "-PT5H30M"
    assert format_trigger(7 * 1440 - 9 * 60) == "-P6DT15H"
    assert format_trigger(-600) == "PT10H"
    assert format_trigger(0) == "PT0S"


def test_render_calendar_structure():
    body = render_calendar(
        [FeedEvent(uid="task-1@x", day=date(2026, 12, 31), summary="Итог, год; всё")],
        name="Календарь ИП",
        now=NOW,
    )
    assert "\n" not in body.replace("\r\n", "")  # только CRLF
    lines = unfold(body)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    assert "VERSION:2.0" in lines and "CALSCALE:GREGORIAN" in lines
    assert any(ln.startswith("PRODID:") for ln in lines)
    assert "X-WR-CALNAME:Календарь ИП" in lines
    (event,) = events_of(lines)
    assert prop(event, "DTSTART;VALUE=DATE") == "20261231"
    assert prop(event, "DTEND;VALUE=DATE") == "20270101"  # следующий день, через год
    assert prop(event, "DTSTAMP") == "20260923T070000Z"
    assert prop(event, "SUMMARY") == "Итог\\, год\\; всё"
    assert "BEGIN:VALARM" not in lines  # без напоминания — без VALARM


# --- Лента по токену ------------------------------------------------------------------------


async def test_feed_contains_undone_items_only(miniapp_api, ics_settings):
    await add_user(OWNER, hour=18)
    ob_id = await add_obligation(OWNER, date(2026, 10, 28))
    await add_obligation(OWNER, date(2026, 10, 1), done=True)
    task_id = await add_task(
        OWNER,
        "Оплатить аренду, склад; офис",
        date(2026, 11, 5),
        remind_offset_days=3,
        remind_hour=9,
        remind_minute=30,
    )
    await add_task(OWNER, "Сделано", date(2026, 10, 2), done_at=NOW)
    await add_task(OWNER, "Удалено", date(2026, 10, 3), deleted_at=NOW)

    res = await feed(miniapp_api, OWNER)
    assert res.status_code == 200
    assert res.headers["content-type"] == "text/calendar; charset=utf-8"
    assert 'filename="calendar-ip.ics"' in res.headers["content-disposition"]

    body = res.content.decode("utf-8")
    events = events_of(unfold(body))
    assert [prop(e, "UID") for e in events] == [
        f"obligation-{ob_id}@vse-uspel.ru",
        f"task-{task_id}@vse-uspel.ru",
    ]
    ob, task = events
    assert prop(ob, "SUMMARY") == "Тестовое годовое обязательство"
    assert prop(ob, "DTSTART;VALUE=DATE") == "20261028"
    assert prop(ob, "DTEND;VALUE=DATE") == "20261029"
    assert prop(ob, "DESCRIPTION") == ("Тестовая норма №1\\nhttps://example.invalid/test-yearly")
    assert prop(ob, "VALARM/TRIGGER") == "-PT6H"  # накануне в 18:00 — час из профиля
    assert prop(task, "SUMMARY") == "Оплатить аренду\\, склад\\; офис"
    assert prop(task, "VALARM/TRIGGER") == "-P2DT14H30M"  # за 3 дня в 9:30
    assert "Сделано" not in body and "Удалено" not in body


async def test_feed_uid_is_stable_between_fetches(miniapp_api, ics_settings):
    await add_user(OWNER)
    await add_obligation(OWNER, date(2026, 10, 28))
    await add_task(OWNER, "Задача", date(2026, 11, 5))

    def uids(body: bytes) -> list[str]:
        return [prop(e, "UID") for e in events_of(unfold(body.decode()))]

    first = await feed(miniapp_api, OWNER)
    second = await feed(miniapp_api, OWNER)
    assert uids(first.content) == uids(second.content)
    assert len(uids(first.content)) == 2


async def test_long_cyrillic_title_folded_by_octets(miniapp_api, ics_settings):
    await add_user(OWNER)
    title = "Оплатить аренду помещения и коммунальные услуги за квартал склад"[:60]
    await add_task(OWNER, title, date(2026, 11, 5))
    body = (await feed(miniapp_api, OWNER)).content
    for line in body.split(b"\r\n"):
        assert len(line) <= 75
        line.decode("utf-8")
    assert f"SUMMARY:{title}" in unfold(body.decode())


async def test_feed_fetched_tracked_once_a_day(miniapp_api, ics_settings):
    await add_user(OWNER)
    await add_task(OWNER, "Задача", date(2026, 11, 5))
    for _ in range(3):
        assert (await feed(miniapp_api, OWNER)).status_code == 200
    async with SessionLocal() as s:
        rows = list(await s.scalars(select(Event).where(Event.name == "ics_feed_fetched")))
    assert [(e.user_id, e.props) for e in rows] == [(OWNER, {"items": 1})]


@pytest.mark.parametrize(
    "token",
    ["garbage", "701-" + "0" * 32, "701", "-" + "a" * 32, "701-" + "A" * 32],
)
async def test_bad_token_404(miniapp_api, ics_settings, token):
    await add_user(OWNER)
    res = await miniapp_api.client.get(f"/api/ics/{token}.ics")
    assert res.status_code == 404
    assert "BEGIN:VCALENDAR" not in res.text


async def test_token_signed_with_other_secret_404(miniapp_api, ics_settings):
    await add_user(OWNER)
    assert (await feed(miniapp_api, OWNER, secret="other-secret")).status_code == 404


async def test_token_of_one_user_does_not_open_another(miniapp_api, ics_settings):
    await add_user(OWNER)
    await add_user(STRANGER)
    await add_task(OWNER, "Моя задача", date(2026, 11, 5))
    await add_task(STRANGER, "Чужая задача", date(2026, 11, 6))

    mine = (await feed(miniapp_api, OWNER)).text
    assert "Моя задача" in mine and "Чужая задача" not in mine

    # Подпись владельца с id другого пользователя не подходит
    sig = ics.make_token(OWNER, SECRET).split("-", 1)[1]
    res = await miniapp_api.client.get(f"/api/ics/{STRANGER}-{sig}.ics")
    assert res.status_code == 404


async def test_feed_for_user_without_items_is_empty_calendar(miniapp_api, ics_settings):
    res = await feed(miniapp_api, OWNER)
    assert res.status_code == 200
    lines = unfold(res.text)
    assert lines[0] == "BEGIN:VCALENDAR" and "BEGIN:VEVENT" not in lines


# --- Ссылка /api/ics/link -------------------------------------------------------------------


async def test_link_requires_initdata(miniapp_api, ics_settings):
    res = await miniapp_api.request("GET", "/api/ics/link", None)
    assert res.status_code == 401


async def test_link_returns_https_and_webcal(miniapp_api, ics_settings):
    res = await miniapp_api.request("GET", "/api/ics/link", OWNER)
    assert res.status_code == 200
    body = res.json()
    token = ics.make_token(OWNER, SECRET)
    assert body == {
        "url": f"{BASE}/api/ics/{token}.ics",
        "webcal_url": f"webcal://vse-uspel.ru/api/ics/{token}.ics",
    }
    # Ссылка открывает ленту без initData
    path = body["url"].removeprefix(BASE)
    assert (await miniapp_api.client.get(path)).status_code == 200


async def test_link_without_secret_503(miniapp_api):
    s = Settings(_env_file=None, max_webhook_secret="", public_base_url=BASE)
    app.dependency_overrides[ics.current_settings] = lambda: s
    try:
        res = await miniapp_api.request("GET", "/api/ics/link", OWNER)
        assert res.status_code == 503
        feed_res = await miniapp_api.client.get(f"/api/ics/{OWNER}-{'0' * 32}.ics")
        assert feed_res.status_code == 404
    finally:
        app.dependency_overrides.pop(ics.current_settings, None)
