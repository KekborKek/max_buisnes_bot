"""T4 (#45): сборка календаря по профилю — экран 5, data-model.md, D22, D24, D25.

Справочники — ТЕСТОВЫЕ ДАННЫЕ из backend/tests/fixtures/, не файлы аналитика. Две записи
фикстуры закреплены тестом загрузчика, поэтому записи для НДС и патента собраны здесь,
в памяти, через dataclasses.replace. Ожидаемые даты записаны явно.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.calendar import dates, loader
from app.calendar.build import ProfileFacts, applies, build_calendar, build_horizon
from app.calendar.reminders import cancel_pending
from app.calendar.types import (
    AppliesIf,
    MissingYearError,
    Reference,
    WorkdayCalendar,
    YearWorkdays,
)
from app.core.db import SessionLocal
from app.core.models import Notification, Profile, User, UserObligation

FIXTURES = Path(__file__).parent / "fixtures"
USER_ID = 202
NOW = datetime(2026, 9, 23, 7, tzinfo=UTC)  # 10:00 по Москве


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _reference() -> Reference:
    """Фикстура + две записи в памяти (ТЕСТОВЫЕ ДАННЫЕ): только для НДС и только для патента."""
    catalog = loader.load_catalog(FIXTURES / "obligations.yaml")
    by_id = {ob.id: ob for ob in catalog.obligations}
    extra = (
        replace(by_id["test_quarterly"], id="test_nds", applies_if=AppliesIf(nds_payer=True)),
        replace(
            by_id["test_yearly"],
            id="test_patent",
            applies_if=AppliesIf(regime=frozenset({"patent"})),
        ),
    )
    return Reference(
        catalog=replace(catalog, obligations=catalog.obligations + extra),
        workdays=loader.load_workdays(FIXTURES / "workdays.yaml"),
        nds=loader.load_nds(FIXTURES / "nds.yaml"),
    )


REF = _reference()


def _with_holiday(ref: Reference, day: date) -> Reference:
    """Тот же справочник, но аналитик добавил праздник `day` в производственный календарь."""
    years = dict(ref.workdays.years)
    y = years[day.year]
    years[day.year] = YearWorkdays(holidays=y.holidays | {day}, workdays=y.workdays)
    return replace(ref, workdays=WorkdayCalendar(years=years))


async def _profile(
    session,
    *,
    regime="usn6",
    income_band="10_20",
    has_employees=False,
    tz="Europe/Moscow",
    reminders=None,
) -> Profile:
    profile = await session.get(Profile, USER_ID)
    if profile is None:
        session.add(User(user_id=USER_ID))
        await session.flush()  # связей в ORM нет — порядок вставки задаём сами
        profile = Profile(user_id=USER_ID)
        session.add(profile)
    profile.regime = regime
    profile.income_band = income_band
    profile.has_employees = has_employees
    profile.timezone = tz
    # как это делает бот после экрана 2: доход за прошлый год и порог из nds.yaml
    profile.nds_payer = dates.nds_payer(income_band, 2025, REF.nds)
    if reminders is not None:
        profile.reminders = reminders
    await session.flush()
    return profile


async def _items(session) -> list[tuple[str, date]]:
    rows = await session.scalars(
        select(UserObligation)
        .where(UserObligation.user_id == USER_ID)
        .order_by(UserObligation.due_date, UserObligation.obligation_id)
    )
    return [(uo.obligation_id, uo.due_date) for uo in rows]


async def _uo(session, obligation_id: str, original: date) -> list[UserObligation]:
    rows = await session.scalars(
        select(UserObligation).where(
            UserObligation.user_id == USER_ID,
            UserObligation.obligation_id == obligation_id,
            UserObligation.original_date == original,
        )
    )
    return list(rows)


async def _notif_snapshot(session) -> list[tuple]:
    rows = await session.scalars(select(Notification).order_by(Notification.id))
    return [(n.id, n.item_id, n.kind, n.send_at, n.status) for n in rows]


async def _pending_kinds(session, item_id: int) -> list[str]:
    rows = await session.scalars(
        select(Notification.kind).where(
            Notification.item_type == "obligation",
            Notification.item_id == item_id,
            Notification.status == "pending",
        )
    )
    return sorted(rows)


# --- applies ----------------------------------------------------------------------------------


def _facts(**kw) -> ProfileFacts:
    base = {
        "regime": "usn6",
        "income_band": "10_20",
        "has_employees": False,
        "nds_payer": None,
        "timezone": "Europe/Moscow",
    }
    return ProfileFacts(**(base | kw))


def _ob(**cond):
    return replace(REF.catalog.obligations[0], applies_if=AppliesIf(**cond))


@pytest.mark.parametrize(
    ("cond", "facts", "expected"),
    [
        ({}, {}, True),
        ({}, {"regime": None, "income_band": None, "has_employees": None}, True),
        ({"regime": frozenset({"usn6", "usn15"})}, {}, True),
        ({"regime": frozenset({"usn6", "usn15"})}, {"regime": "patent"}, False),
        ({"regime": frozenset({"usn6"})}, {"regime": None}, False),  # D24
        ({"income_band": frozenset({"20_60", "gt60"})}, {"income_band": "10_20"}, False),
        ({"income_band": frozenset({"20_60", "gt60"})}, {"income_band": "gt60"}, True),
        ({"has_employees": False}, {}, True),
        ({"has_employees": False}, {"has_employees": True}, False),
        ({"has_employees": False}, {"has_employees": None}, False),  # D24
        ({"nds_payer": True}, {"nds_payer": True}, True),
        ({"nds_payer": True}, {"nds_payer": False}, False),
        ({"nds_payer": True}, {"nds_payer": None}, False),  # D25
        ({"nds_payer": False}, {"nds_payer": None}, False),
        ({"regime": frozenset({"usn6"}), "nds_payer": True}, {"nds_payer": True}, True),
        ({"regime": frozenset({"usn15"}), "nds_payer": True}, {"nds_payer": True}, False),
    ],
)
def test_applies(cond, facts, expected):
    assert applies(_ob(**cond), _facts(**facts)) is expected


# --- build_horizon ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "horizon"),
    [
        (date(2026, 1, 1), date(2027, 12, 31)),
        (date(2026, 9, 23), date(2027, 12, 31)),
        (date(2026, 12, 31), date(2027, 12, 31)),
        (date(2027, 1, 1), date(2028, 12, 31)),
    ],
)
def test_build_horizon_is_end_of_next_year(today, horizon):
    assert build_horizon(today) == horizon


# --- build_calendar ---------------------------------------------------------------------------

EXPECTED_USN6 = [
    ("test_quarterly", date(2026, 10, 25)),
    ("test_yearly", date(2026, 12, 28)),
    ("test_quarterly", date(2027, 1, 25)),
    ("test_quarterly", date(2027, 4, 25)),
    ("test_quarterly", date(2027, 7, 25)),
    ("test_quarterly", date(2027, 10, 25)),
    ("test_yearly", date(2027, 12, 28)),
]


async def test_usn6_no_employees_10_20_gives_expected_set():
    async with SessionLocal() as session:
        profile = await _profile(session)
        result = await build_calendar(session, USER_ID, now=NOW, reference=REF)

        assert await _items(session) == EXPECTED_USN6  # ни НДС, ни патента
        assert result.created == 7
        assert result.total == 7
        assert result.this_year == 2
        assert result.nearest_obligation_id == "test_quarterly"
        assert result.nearest_due_date == date(2026, 10, 25)
        assert profile.calendar_built_at is not None

        kinds = await session.execute(
            select(UserObligation.obligation_id, Notification.kind, func.count())
            .join(UserObligation, UserObligation.id == Notification.item_id)
            .where(Notification.status == "pending")
            .group_by(UserObligation.obligation_id, Notification.kind)
        )
        assert sorted(kinds.all()) == [
            ("test_quarterly", "d1", 5),
            ("test_quarterly", "d7", 5),
            ("test_quarterly", "overdue", 5),
            ("test_yearly", "d1", 2),
            ("test_yearly", "d30", 2),  # d30 — только needs_prep
            ("test_yearly", "d7", 2),
            ("test_yearly", "overdue", 2),
        ]


async def test_nds_payer_gets_nds_block():
    async with SessionLocal() as session:
        await _profile(session, income_band="gt60")
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        assert sum(1 for ob, _ in await _items(session) if ob == "test_nds") == 5


async def test_repeat_build_is_idempotent():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        items = await _items(session)
        notifs = await _notif_snapshot(session)

        again = await build_calendar(session, USER_ID, now=NOW, reference=REF)

        assert again.created == 0
        assert again.total == 7
        assert await _items(session) == items
        assert await _notif_snapshot(session) == notifs


async def test_notifications_in_past_are_not_created():
    now = utc(2026, 12, 1, 7)
    async with SessionLocal() as session:
        await _profile(session)
        result = await build_calendar(session, USER_ID, now=now, reference=REF)
        assert result.this_year == 1
        [dec] = await _uo(session, "test_yearly", date(2026, 12, 28))
        assert await _pending_kinds(session, dec.id) == ["d1", "d7", "overdue"]  # d30 — 28.11
        send_ats = (await session.scalars(select(Notification.send_at))).all()
        assert send_ats and all(at.replace(tzinfo=UTC) > now for at in send_ats)


@pytest.mark.parametrize(
    ("tz", "nearest"),
    [("Europe/Moscow", date(2026, 10, 25)), ("Asia/Vladivostok", date(2026, 12, 28))],
)
async def test_today_is_in_user_timezone(tz, nearest):
    now = utc(2026, 10, 25, 14, 30)  # Москва — 25 октября, Владивосток — уже 26-е
    async with SessionLocal() as session:
        await _profile(session, tz=tz)
        result = await build_calendar(session, USER_ID, now=now, reference=REF)
        assert result.nearest_due_date == nearest


async def test_rebuild_with_other_profile_keeps_marks():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        [done] = await _uo(session, "test_yearly", date(2026, 12, 28))
        [gone] = await _uo(session, "test_yearly", date(2027, 12, 28))
        done_at = utc(2026, 9, 23, 8)
        done.done_at = done_at
        await cancel_pending(session, "obligation", done.id)
        gone_d30 = await session.scalar(
            select(Notification).where(Notification.item_id == gone.id, Notification.kind == "d30")
        )
        gone_d30.status = "sent"
        await session.flush()

        # «Собрать заново»: режим сменился на патент, test_yearly профилю больше не подходит
        await _profile(session, regime="patent")
        result = await build_calendar(session, USER_ID, now=NOW, reference=REF)

        [kept] = await _uo(session, "test_yearly", date(2026, 12, 28))
        assert kept.id == done.id
        assert kept.done_at.replace(tzinfo=UTC) == done_at
        assert await _pending_kinds(session, kept.id) == []
        assert await _uo(session, "test_yearly", date(2027, 12, 28)) == []
        # удалено вместе со всеми уведомлениями: даже если SQLite отдал его id новой записи,
        # чужой истории (sent) у неё нет, и висящих уведомлений не осталось
        live_ids = set((await session.scalars(select(UserObligation.id))).all())
        notifs = (await session.scalars(select(Notification))).all()
        assert {n.item_id for n in notifs} <= live_ids
        assert [n for n in notifs if n.item_id == gone.id and n.status == "sent"] == []
        assert result.created == 2  # test_patent × 2
        assert result.nearest_obligation_id == "test_quarterly"

        # и обратно: отмеченное не дублируется, неотмеченное возвращается
        await _profile(session, regime="usn6")
        back = await build_calendar(session, USER_ID, now=NOW, reference=REF)
        assert back.created == 1
        assert await _items(session) == EXPECTED_USN6
        [still] = await _uo(session, "test_yearly", date(2026, 12, 28))
        assert still.id == done.id and still.done_at is not None
        assert await _pending_kinds(session, still.id) == []


async def test_nearest_skips_done():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        [oct25] = await _uo(session, "test_quarterly", date(2026, 10, 25))
        oct25.done_at = NOW
        await session.flush()
        result = await build_calendar(session, USER_ID, now=NOW, reference=REF)
        assert result.nearest_due_date == date(2026, 12, 28)
        assert result.this_year == 2  # отмеченное видно в мини-приложении — считается


async def test_past_undone_item_stays_overdue_on_rebuild():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        later = utc(2026, 11, 1, 7)
        result = await build_calendar(session, USER_ID, now=later, reference=REF)
        assert len(await _uo(session, "test_quarterly", date(2026, 10, 25))) == 1
        assert result.created == 0
        assert result.total == 6  # 25.10 уже в прошлом — вне горизонта сборки


async def test_rebuild_after_timezone_change_moves_pending():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        count = len(await _notif_snapshot(session))

        await _profile(session, tz="Asia/Vladivostok")
        await build_calendar(session, USER_ID, now=NOW, reference=REF)

        assert len(await _notif_snapshot(session)) == count
        [dec] = await _uo(session, "test_yearly", date(2026, 12, 28))
        d7 = await session.scalar(
            select(Notification).where(Notification.item_id == dec.id, Notification.kind == "d7")
        )
        assert d7.send_at.replace(tzinfo=UTC) == utc(2026, 12, 21, 0)


async def test_rebuild_does_not_resend_sent():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        [dec] = await _uo(session, "test_yearly", date(2026, 12, 28))
        d30 = await session.scalar(
            select(Notification).where(Notification.item_id == dec.id, Notification.kind == "d30")
        )
        d30.status = "sent"
        await session.flush()
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        assert await _pending_kinds(session, dec.id) == ["d1", "d7", "overdue"]


# --- срок сдвинулся после правки производственного календаря ----------------------------------

MOVED = _with_holiday(REF, date(2026, 12, 28))  # 28.12.2026 стал нерабочим → срок 29.12


async def test_moved_due_date_updates_undone_in_place():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        [before] = await _uo(session, "test_yearly", date(2026, 12, 28))
        count = len(await _notif_snapshot(session))

        result = await build_calendar(session, USER_ID, now=NOW, reference=MOVED)

        [after] = await _uo(session, "test_yearly", date(2026, 12, 28))
        assert after.id == before.id
        assert after.due_date == date(2026, 12, 29)
        assert result.created == 0
        assert len(await _notif_snapshot(session)) == count
        d7 = await session.scalar(
            select(Notification).where(Notification.item_id == after.id, Notification.kind == "d7")
        )
        assert d7.send_at.replace(tzinfo=UTC) == utc(2026, 12, 22, 7)


async def test_moved_due_date_does_not_duplicate_done():
    async with SessionLocal() as session:
        await _profile(session)
        await build_calendar(session, USER_ID, now=NOW, reference=REF)
        [done] = await _uo(session, "test_yearly", date(2026, 12, 28))
        done.done_at = NOW
        await cancel_pending(session, "obligation", done.id)
        await session.flush()

        result = await build_calendar(session, USER_ID, now=NOW, reference=MOVED)

        [still] = await _uo(session, "test_yearly", date(2026, 12, 28))
        assert still.id == done.id
        assert still.due_date == date(2026, 12, 28)  # отмеченное не трогаем
        assert still.done_at is not None
        assert result.created == 0
        assert await _pending_kinds(session, still.id) == []
        assert ("test_yearly", date(2026, 12, 29)) not in await _items(session)


# --- ошибки -------------------------------------------------------------------------------------


async def test_missing_workdays_year_fails_before_writing():
    async with SessionLocal() as session:
        await _profile(session)
        with pytest.raises(MissingYearError) as err:
            # горизонт — 2028, а в тестовом производственном календаре только 2026–2027
            await build_calendar(session, USER_ID, now=utc(2027, 6, 1, 7), reference=REF)
        assert err.value.year == 2028
        assert await _items(session) == []
        assert await _notif_snapshot(session) == []


async def test_no_profile_is_lookup_error():
    async with SessionLocal() as session:
        with pytest.raises(LookupError):
            await build_calendar(session, USER_ID, now=NOW, reference=REF)
