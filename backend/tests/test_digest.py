"""T14-12 (#77): экран 12 — сводка в понедельник (docs/screens/should-12-13-19.md).

Справочник — фикстура backend/tests/fixtures/obligations.yaml (ТЕСТОВЫЕ ДАННЫЕ). Неделя —
понедельник 26 октября 2026 – воскресенье 1 ноября. Моменты отправки в UTC записаны явно,
а не вычислены той же формулой, что в коде.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.bot.handlers import common
from app.calendar import digest
from app.calendar.reminders import tick
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Event, Notification, Profile, Task, User, UserObligation
from app.core.texts import t
from tests.conftest import FakeMax
from tests.test_task_chat import press, write

pytestmark = pytest.mark.usefixtures("fixture_reference")

USER = 42  # совпадает с user_id в фикстурах апдейтов
BOT = "pareto_calendar_bot"
MSK = "Europe/Moscow"
YEKT = "Asia/Yekaterinburg"
VLAT = "Asia/Vladivostok"
YEARLY = "Тестовое годовое обязательство"
QUARTERLY = "Тестовое квартальное обязательство"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
MONDAY = date(2026, 10, 26)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# 10:00 в понедельник 26 октября по поясу пользователя
AT_10 = {MSK: utc(2026, 10, 26, 7), YEKT: utc(2026, 10, 26, 5), VLAT: utc(2026, 10, 26, 0)}
MSK_10 = AT_10[MSK]
MINUTE = timedelta(minutes=1)
DAY = timedelta(days=1)

TWO_ITEMS = (
    f"Неделя 26 октября – 1 ноября. Два срока: 28 октября — {YEARLY}; 30 октября — оплатить аренду."
)


@pytest.fixture
def bot_username(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", BOT)


@pytest.fixture
def no_bot_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")


# --- помощники --------------------------------------------------------------------------------


async def _user(user_id=USER, tz=MSK, *, built=True, reminders=None) -> None:
    """`built`: True — календарь собран давно, False — не собран, datetime — собран тогда."""
    built_at = built if isinstance(built, datetime) else (LONG_AGO if built else None)
    async with SessionLocal() as s:
        s.add(User(user_id=user_id))
        await s.flush()
        profile = Profile(
            user_id=user_id,
            timezone=tz,
            income_band="lt10",
            regime="usn6",
            has_employees=False,
            calendar_built_at=built_at,
        )
        if reminders is not None:
            profile.reminders = reminders
        s.add(profile)
        await s.commit()


async def _uo(due: date, *, ob="test_yearly", user_id=USER, done=False) -> int:
    async with SessionLocal() as s:
        uo = UserObligation(
            user_id=user_id,
            obligation_id=ob,
            rule_version=1,
            original_date=due,
            due_date=due,
            done_at=LONG_AGO if done else None,
        )
        s.add(uo)
        await s.commit()
        return uo.id


async def _task(title: str, due: date, *, user_id=USER, done=False, deleted=False) -> int:
    async with SessionLocal() as s:
        task = Task(
            user_id=user_id,
            title=title,
            due_date=due,
            done_at=LONG_AGO if done else None,
            deleted_at=LONG_AGO if deleted else None,
        )
        s.add(task)
        await s.commit()
        return task.id


async def _week_with_two_items(user_id=USER) -> tuple[int, int]:
    """Обязательство 28 октября и задача 30 октября. → (uo.id, task.id)."""
    return (
        await _uo(date(2026, 10, 28), user_id=user_id),
        await _task("оплатить аренду", date(2026, 10, 30), user_id=user_id),
    )


async def _digests() -> list[Notification]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Notification).where(Notification.kind == "digest"))
        return list(rows)


async def _events(name: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Event).where(Event.name == name).order_by(Event.id))
        return [e.props for e in rows]


async def _profile_reminders(user_id=USER) -> dict:
    async with SessionLocal() as s:
        return dict((await s.get(Profile, user_id)).reminders)


class FailingMax(FakeMax):
    async def send_message(self, *args, **kwargs):
        raise RuntimeError("MAX недоступен")


# --- когда уходит -----------------------------------------------------------------------------


@pytest.mark.parametrize("tz", [MSK, YEKT, VLAT])
async def test_sent_on_monday_at_hour_in_user_timezone(fake_max, no_bot_name, tz):
    await _user(tz=tz)
    await _week_with_two_items()
    at = AT_10[tz]

    # воскресенье в тот же час, понедельник за минуту до часа, вторник — ничего
    for moment in (at - DAY, at - MINUTE, at + DAY):
        await tick(moment, fake_max)
    assert fake_max.sent == []

    await tick(at, fake_max)

    assert len(fake_max.sent) == 1
    assert fake_max.sent[0]["user_id"] == USER
    assert fake_max.sent[0]["text"] == TWO_ITEMS


async def test_hour_from_profile_settings(fake_max, no_bot_name):
    await _user(reminders={"d30": True, "d7": True, "hour": 18})
    await _week_with_two_items()

    await tick(MSK_10, fake_max)  # 10:00 МСК — рано
    assert fake_max.sent == []

    await tick(utc(2026, 10, 26, 15), fake_max)  # 18:00 МСК
    assert len(fake_max.sent) == 1


async def test_catches_up_only_on_the_same_monday(fake_max, no_bot_name):
    """Планировщик стоял с утра: вечером понедельника догоняем, во вторник — уже нет."""
    await _user()
    await _week_with_two_items()

    await tick(utc(2026, 10, 26, 21, 30), fake_max)  # вторник 00:30 МСК
    assert fake_max.sent == []

    await tick(utc(2026, 10, 26, 20, 59), fake_max)  # понедельник 23:59 МСК
    assert len(fake_max.sent) == 1


async def test_not_sent_before_calendar_is_built(fake_max, no_bot_name):
    await _user(built=False)
    await _week_with_two_items()

    await tick(MSK_10, fake_max)

    assert fake_max.sent == []
    assert await _digests() == []


# --- что внутри -------------------------------------------------------------------------------


async def test_text_buttons_and_analytics(fake_max, bot_username):
    await _user()
    uo_id, task_id = await _week_with_two_items()

    await tick(MSK_10, fake_max)

    msg = fake_max.sent[0]
    assert msg["text"] == TWO_ITEMS
    buttons = msg["attachments"][0]["payload"]["buttons"]
    assert buttons == [
        [
            {"type": "open_app", "text": "Открыть календарь", "web_app": BOT},
            {"type": "callback", "text": "Без сводки", "payload": "digest:off"},
        ]
    ]
    assert await _events("reminder_sent") == [
        {"kind": "digest", "item_id": uo_id, "item_type": "obligation", "grouped": True},
        {"kind": "digest", "item_id": task_id, "item_type": "task", "grouped": True},
    ]


async def test_only_undone_items_of_this_week(fake_max, no_bot_name):
    await _user()
    await _uo(date(2026, 10, 27), done=True)  # отмечено
    await _uo(date(2026, 10, 25), ob="test_quarterly")  # воскресенье прошлой недели
    await _uo(date(2026, 11, 2), ob="test_quarterly")  # понедельник следующей
    await _task("удалённая", date(2026, 10, 29), deleted=True)
    await _task("сделанная", date(2026, 10, 29), done=True)
    task_id = await _task("сдать отчёт", date(2026, 11, 1))  # воскресенье этой недели

    await tick(MSK_10, fake_max)

    # одно событие: «1 срок» — форма из reminder.count_n (вопрос к UX в PR)
    expected = "Неделя 26 октября – 1 ноября. 1 срок: 1 ноября — сдать отчёт."
    assert fake_max.sent[0]["text"] == expected
    assert await _events("reminder_sent") == [
        {"kind": "digest", "item_id": task_id, "item_type": "task", "grouped": False}
    ]


async def test_empty_week_sends_nothing_and_is_decided_once(fake_max, no_bot_name, monkeypatch):
    """Пустая неделя: одна строка `cancelled`, события больше не перечитываются, а задача,
    заведённая в тот же понедельник позже, сводку не вызывает (сценарий Б ревью #79)."""
    await _user()
    await _uo(date(2026, 10, 28), done=True)
    await _uo(date(2026, 11, 2), ob="test_quarterly")
    reads = []
    real_load = digest.load_week_items

    async def counting_load(*args, **kwargs):
        reads.append(args[2])
        return await real_load(*args, **kwargs)

    monkeypatch.setattr(digest, "load_week_items", counting_load)

    await tick(MSK_10, fake_max)
    await tick(MSK_10 + MINUTE, fake_max)
    await _task("оплатить аренду", date(2026, 10, 30))  # 15:00 МСК — задача на эту неделю
    await tick(utc(2026, 10, 26, 12, 1), fake_max)

    assert fake_max.sent == []
    [row] = await _digests()
    assert (row.status, row.item_id) == ("cancelled", 20261026)
    assert reads == [USER]  # события прочитаны один раз
    assert await _events("reminder_sent") == []
    assert await _events("error") == []


async def test_no_digest_on_the_monday_of_onboarding(fake_max, no_bot_name):
    """Сценарий А ревью #79: календарь собран в понедельник в 14:00 МСК, после 10:00 —
    в этот понедельник сводки нет, человек только что видел экран 5; в следующий — есть."""
    await _user(built=utc(2026, 10, 26, 11))
    await _week_with_two_items()
    await _uo(date(2026, 11, 3), ob="test_quarterly")

    await tick(utc(2026, 10, 26, 11, 1), fake_max)
    await tick(utc(2026, 10, 26, 15), fake_max)
    assert fake_max.sent == []
    assert await _digests() == []

    await tick(MSK_10 + 7 * DAY, fake_max)
    assert len(fake_max.sent) == 1


async def test_calendar_built_before_hour_on_monday_gets_digest(fake_max, no_bot_name):
    await _user(built=utc(2026, 10, 26, 6))  # 09:00 МСК понедельника
    await _week_with_two_items()

    await tick(MSK_10, fake_max)

    assert [m["text"] for m in fake_max.sent] == [TWO_ITEMS]


@pytest.mark.parametrize("hour", ["abc", 25, [10]])
async def test_broken_hour_skips_only_that_user(fake_max, no_bot_name, caplog, hour):
    await _user(reminders={"d30": True, "d7": True, "hour": hour})
    await _week_with_two_items()
    other = 43
    await _user(user_id=other)
    await _week_with_two_items(user_id=other)

    await tick(MSK_10, fake_max)

    assert [m["user_id"] for m in fake_max.sent] == [other]
    assert "сводка 42" in caplog.text


async def test_vladivostok_hour_9_is_sunday_evening_utc(fake_max, no_bot_name):
    """09:00 понедельника во Владивостоке — 23:00 воскресенья по UTC: сводка уходит."""
    await _user(tz=VLAT, reminders={"d30": True, "d7": True, "hour": 9})
    await _week_with_two_items()

    await tick(utc(2026, 10, 25, 22, 59), fake_max)
    assert fake_max.sent == []

    await tick(utc(2026, 10, 25, 23), fake_max)
    assert [m["text"] for m in fake_max.sent] == [TWO_ITEMS]


def test_count_words_capitalized_for_many():
    items = [
        digest.DigestItem("task", n, f"задача {n}", date(2026, 10, 26) + timedelta(days=n % 7))
        for n in range(5)
    ]
    text, _ = digest.render_digest(sorted(items, key=lambda i: i.due_date), MONDAY, MONDAY)
    assert text.startswith("Неделя 26 октября – 1 ноября. 5 сроков: 26 октября — задача 0; ")


@pytest.mark.parametrize(
    ("monday", "today", "expected"),
    [
        (date(2026, 10, 5), date(2026, 10, 5), "5–11 октября"),
        (date(2026, 10, 26), date(2026, 10, 26), "26 октября – 1 ноября"),
        (date(2026, 12, 28), date(2026, 12, 28), "28 декабря – 3 января 2027"),
        (date(2027, 1, 4), date(2026, 12, 31), "4–10 января 2027"),
    ],
)
def test_format_range(monday, today, expected):
    assert digest.format_range(monday, today) == expected


# --- не больше одной в неделю ------------------------------------------------------------------


async def test_repeated_ticks_send_one_digest(fake_max, no_bot_name):
    await _user()
    await _week_with_two_items()

    for moment in (MSK_10, MSK_10, MSK_10 + MINUTE, utc(2026, 10, 26, 15)):
        await tick(moment, fake_max)

    assert len(fake_max.sent) == 1
    [row] = await _digests()
    assert (row.status, row.item_type, row.item_id) == ("sent", "week", 20261026)
    assert len(await _events("reminder_sent")) == 2


async def test_next_monday_gets_next_digest(fake_max, no_bot_name):
    await _user()
    await _week_with_two_items()
    await _uo(date(2026, 11, 3), ob="test_quarterly")

    await tick(MSK_10, fake_max)
    await tick(MSK_10 + 7 * DAY, fake_max)

    assert len(fake_max.sent) == 2
    assert fake_max.sent[1]["text"].startswith("Неделя 2–8 ноября. 1 срок: 3 ноября — ")


async def test_crash_after_claim_does_not_resend(fake_max, no_bot_name):
    """Процесс упал между «забрать» и «записать»: строка pending, второй сводки нет."""
    await _user()
    await _week_with_two_items()
    async with SessionLocal() as s:
        s.add(
            Notification(
                user_id=USER,
                item_type="week",
                item_id=20261026,
                kind="digest",
                send_at=MSK_10,
                status="pending",
                attempts=1,
            )
        )
        await s.commit()

    for moment in (MSK_10, MSK_10 + 2 * timedelta(hours=1), utc(2026, 10, 26, 20)):
        await tick(moment, fake_max)

    assert fake_max.sent == []
    [row] = await _digests()
    assert row.status == "pending"  # обычная отправка напоминаний её не забрала и не отменила


async def test_send_failure_is_not_retried(no_bot_name):
    await _user()
    await _week_with_two_items()

    await tick(MSK_10, FailingMax())
    [row] = await _digests()
    assert row.status == "failed"
    assert await _events("error") == [{"where": "digest_send", "kind": "send_failed"}]

    ok = FakeMax()
    await tick(MSK_10 + timedelta(hours=1), ok)
    assert ok.sent == []
    assert await _events("reminder_sent") == []


async def test_regular_reminders_still_sent_with_digest(fake_max, no_bot_name):
    """В понедельник в 10:00 уходят и обычное d1, и сводка — по разу; сводка их не путает."""
    await _user()
    await _week_with_two_items()
    tuesday_id = await _uo(date(2026, 10, 27), ob="test_quarterly")
    async with SessionLocal() as s:
        s.add(
            Notification(
                user_id=USER,
                item_type="obligation",
                item_id=tuesday_id,
                kind="d1",
                send_at=MSK_10,
                status="pending",
                attempts=0,
            )
        )
        await s.commit()

    await tick(MSK_10, fake_max)
    await tick(MSK_10 + MINUTE, fake_max)

    assert len(fake_max.sent) == 2
    assert fake_max.sent[0]["text"].startswith("Завтра, 27 октября — ")
    assert fake_max.sent[1]["text"].startswith("Неделя 26 октября – 1 ноября. Три срока: ")
    kinds = [e["kind"] for e in await _events("reminder_sent")]
    assert kinds == ["d1", "digest", "digest", "digest"]


# --- «Без сводки» -----------------------------------------------------------------------------


async def test_digest_off_flag_means_no_digest(fake_max, no_bot_name):
    await _user(reminders={"d30": True, "d7": True, "hour": 10, "digest": False})
    await _week_with_two_items()

    await tick(MSK_10, fake_max)

    assert fake_max.sent == []
    assert await _digests() == []


async def test_off_button_sets_flag_and_repeat_is_harmless(fake_max, no_bot_name):
    await _user(reminders={"d30": False, "d7": True, "hour": 18})
    await _week_with_two_items()

    await process_update(press("digest:off"), fake_max)
    await process_update(press("digest:off"), fake_max)

    assert [m["text"] for m in fake_max.sent] == [t("digest.off_ok")] * 2
    assert await _profile_reminders() == {"d30": False, "d7": True, "hour": 18, "digest": False}
    assert await _events("reminder_clicked") == [{"kind": "digest", "action": "off"}] * 2

    fake_max.sent.clear()
    await tick(utc(2026, 10, 26, 15), fake_max)  # 18:00 МСК понедельника
    assert fake_max.sent == []


# --- /demo_remind digest ----------------------------------------------------------------------


@pytest.fixture
def wednesday(monkeypatch):
    monkeypatch.setattr(common, "now", lambda: utc(2026, 10, 28, 9))  # среда 12:00 МСК


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", [USER])


async def test_demo_digest_for_admin(fake_max, no_bot_name, wednesday, admin):
    await _user()
    await _week_with_two_items()

    await process_update(write("/demo_remind digest"), fake_max)

    msg = fake_max.sent[-1]
    assert msg["text"] == TWO_ITEMS
    assert msg["attachments"] == digest.digest_keyboard()
    assert await _digests() == []  # демо не трогает настоящее расписание
    assert await _events("reminder_sent") == []


async def test_demo_digest_empty_week(fake_max, no_bot_name, wednesday, admin):
    await _user()
    await _uo(date(2026, 11, 2))

    await process_update(write("/demo_remind DIGEST"), fake_max)

    assert fake_max.sent[-1]["text"] == t("demo.digest_empty")


async def test_demo_digest_for_non_admin_is_unknown(fake_max, no_bot_name, wednesday, monkeypatch):
    monkeypatch.setattr(get_settings(), "support_url", "")
    await _user()
    await _week_with_two_items()

    await process_update(write("/demo_remind digest"), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")
