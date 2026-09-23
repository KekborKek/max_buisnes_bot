"""T0 (#35): таблицы календаря по docs/spec/data-model.md §2.

Базы — на tmp_path: файл общей тестовой базы переживает прогоны, а здесь важна чистая схема.
"""

from datetime import date

import pytest
from sqlalchemy import insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import models
from app.core.db import Base, create_engine

CALENDAR_TABLES = {"profiles", "user_obligations", "tasks", "notifications", "wrong_date_reports"}
OLD_TABLES = ("users", "dialog_states", "processed_updates", "events")


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


@pytest.fixture
async def engine(tmp_path):
    eng = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema.db'}")
    yield eng
    await eng.dispose()


async def _create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def test_calendar_tables_created_on_empty_db(engine):
    await _create_all(engine)

    assert CALENDAR_TABLES <= await _table_names(engine)


async def test_calendar_tables_added_to_db_with_users_and_events(engine):
    old = [Base.metadata.tables[name] for name in OLD_TABLES]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=old))
        await conn.execute(insert(models.User).values(user_id=1, name="Тестовый"))
        await conn.execute(insert(models.Event).values(user_id=1, name="bot_started", props={}))
    assert not CALENDAR_TABLES & await _table_names(engine)

    await _create_all(engine)

    assert CALENDAR_TABLES <= await _table_names(engine)
    async with engine.connect() as conn:
        users = (await conn.execute(select(models.User.user_id))).scalars().all()
        events = (await conn.execute(select(models.Event.name))).scalars().all()
    assert users == [1]
    assert events == ["bot_started"]


async def test_user_obligation_unique_key_blocks_duplicates(engine):
    await _create_all(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def row(due: date) -> models.UserObligation:
        return models.UserObligation(
            user_id=1,
            obligation_id="test_obligation",
            rule_version=1,
            original_date=due,
            due_date=due,
        )

    async with session_factory() as session:
        session.add(models.User(user_id=1))
        await session.commit()
        session.add(row(date(2030, 3, 4)))
        await session.commit()

        session.add(row(date(2030, 3, 4)))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # та же запись справочника на другую дату — другое событие календаря
        session.add(row(date(2030, 6, 4)))
        await session.commit()
        rows = (await session.execute(select(models.UserObligation))).scalars().all()
    assert len(rows) == 2


async def test_profile_created_on_start_without_answers(engine):
    """Профиль появляется на первом /start, ответы онбординга приходят позже."""
    await _create_all(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # связи relationship нет — порядок INSERT не гарантирован, пользователя пишем первым
        session.add(models.User(user_id=7))
        await session.flush()
        session.add(models.Profile(user_id=7))
        await session.commit()
        profile = await session.get(models.Profile, 7)

    assert profile is not None
    assert profile.income_band is None and profile.regime is None
    assert profile.has_employees is None and profile.nds_payer is None
    assert profile.timezone == "Europe/Moscow"
    assert profile.reminders == {"d30": True, "d7": True, "hour": 10}
    assert profile.started_at is not None
    assert profile.calendar_built_at is None


async def test_notification_has_scheduler_index(engine):
    await _create_all(engine)
    async with engine.connect() as conn:
        indexes = await conn.run_sync(lambda c: inspect(c).get_indexes("notifications"))
    assert {"status", "send_at"} == set(
        next(i["column_names"] for i in indexes if i["name"] == "ix_notifications_status_send_at")
    )


def test_no_new_event_named_tables_or_models():
    """Имя Event занято аналитикой: сущности календаря так не называются."""
    table_names = set(Base.metadata.tables)
    assert {n for n in table_names if n.startswith("event")} == {"events"}
    model_names = {m.class_.__name__ for m in Base.registry.mappers}
    assert {n for n in model_names if n.startswith("Event")} == {"Event"}
