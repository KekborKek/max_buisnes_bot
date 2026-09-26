"""#20: жизненный цикл БД — лёгкая миграция схемы при старте и чистка processed_updates.

Сверка схемы проверяется на собственной тестовой MetaData и свежем файле базы в tmp_path:
тесты не должны ломаться от будущих правок настоящих моделей. С настоящими моделями —
один интеграционный тест: актуальная база не даёт ни ALTER, ни тревог в логе.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    event,
    select,
    text,
)
from sqlalchemy.dialects import sqlite

from app import main
from app.core import db
from app.core.config import Settings
from app.core.db import (
    DbColumn,
    SessionLocal,
    create_engine,
    init_db,
    plan_schema_changes,
    purge_processed_updates,
    sqlite_affinity,
    sync_schema,
)
from app.core.models import ProcessedUpdate

LOGGER = "app.core.db"


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
async def tmp_engine(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    yield engine
    await engine.dispose()


async def _start(engine, metadata: MetaData) -> list[str]:
    """То же, что init_db, но на тестовой MetaData."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return await sync_schema(engine, metadata)


async def _execute(engine, *statements: str) -> None:
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))


async def _columns(engine, table: str) -> dict[str, dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).mappings()
        return {r["name"]: dict(r) for r in rows}


def _count_alters(engine) -> list[str]:
    """Все ALTER, которые движок отправит в базу."""
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _spy(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("ALTER"):
            seen.append(statement)

    return seen


def _problems(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.ERROR]


# --- Безопасные недостающие колонки ------------------------------------------------------


def _items_metadata() -> MetaData:
    md = MetaData()
    Table("owners", md, Column("id", Integer, primary_key=True))
    Table(
        "items",
        md,
        Column("id", Integer, primary_key=True),
        Column("title", String(20), nullable=False),
        # новые колонки — все добавляются безопасно
        Column("note", String(255)),
        Column("priority", Integer, nullable=False, default=1),
        Column("tz", String(64), nullable=False, default="Europe/Moscow"),
        Column("flag", Boolean, nullable=False, server_default=text("0")),
        Column("owner_id", Integer, ForeignKey("owners.id", ondelete="CASCADE"), index=True),
    )
    return md


async def test_old_schema_gets_safe_columns_and_keeps_rows(tmp_engine, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await _execute(
        tmp_engine,
        "CREATE TABLE items (id INTEGER PRIMARY KEY, title VARCHAR(20) NOT NULL)",
        "INSERT INTO items (id, title) VALUES (1, 'старая строка'), (2, 'ещё одна')",
    )

    applied = await _start(tmp_engine, _items_metadata())

    assert len(applied) == 5
    cols = await _columns(tmp_engine, "items")
    assert {"note", "priority", "tz", "flag", "owner_id"} <= cols.keys()
    assert cols["priority"]["notnull"] == 1
    async with tmp_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, title, note, priority, tz, flag, owner_id FROM items ORDER BY id")
            )
        ).all()
        indexes = {r[1] for r in await conn.execute(text("PRAGMA index_list(items)"))}
        fks = (await conn.execute(text("PRAGMA foreign_key_list(items)"))).mappings().all()
    assert rows == [
        (1, "старая строка", None, 1, "Europe/Moscow", 0, None),
        (2, "ещё одна", None, 1, "Europe/Moscow", 0, None),
    ]
    assert "ix_items_owner_id" in indexes
    assert [(fk["table"], fk["to"], fk["on_delete"]) for fk in fks] == [("owners", "id", "CASCADE")]

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 5
    assert any("items" in m and "priority" in m for m in warnings)
    assert not _problems(caplog)


async def test_restart_after_migration_is_noop(tmp_engine, caplog):
    await _execute(tmp_engine, "CREATE TABLE items (id INTEGER PRIMARY KEY, title VARCHAR(20))")
    md = _items_metadata()
    await _start(tmp_engine, md)

    caplog.clear()
    caplog.set_level(logging.WARNING, logger=LOGGER)
    alters = _count_alters(tmp_engine)
    assert await _start(tmp_engine, md) == []
    assert alters == []
    assert not [r for r in caplog.records if r.name == LOGGER]


async def test_failed_alter_is_logged_and_others_applied(tmp_engine, caplog):
    """SQLite не даёт добавить колонку с неконстантным default в непустую таблицу —
    это ошибка в лог, не падение, и остальные колонки всё равно добавляются."""
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await _execute(
        tmp_engine,
        "CREATE TABLE items (id INTEGER PRIMARY KEY)",
        "INSERT INTO items (id) VALUES (1)",
    )
    md = MetaData()
    Table(
        "items",
        md,
        Column("id", Integer, primary_key=True),
        Column("stamp", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        Column("note", String(255)),
    )

    applied = await _start(tmp_engine, md)

    assert len(applied) == 1 and "note" in applied[0]
    assert "stamp" not in await _columns(tmp_engine, "items")
    assert any("stamp" in r.getMessage() for r in _problems(caplog))


# --- Несовместимые расхождения -----------------------------------------------------------


async def test_incompatible_drift_logs_error_and_touches_nothing(tmp_engine, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await _execute(
        tmp_engine,
        "CREATE TABLE items (id INTEGER PRIMARY KEY, title INTEGER, label TEXT NOT NULL, "
        "legacy TEXT)",
        "INSERT INTO items (id, title, label, legacy) VALUES (1, 7, 'метка', 'старое')",
    )
    md = MetaData()
    Table(
        "items",
        md,
        Column("id", Integer, primary_key=True),
        Column("title", String(20)),  # в базе INTEGER — другой тип
        Column("label", String(20), nullable=True),  # в базе NOT NULL
        Column("created", DateTime, nullable=False, default=_now),  # NOT NULL без скаляра
        Column("rule", Integer, nullable=False),  # NOT NULL без default
        Column("code", String(8), unique=True),  # UNIQUE через ADD COLUMN не добавить
        Column("payload", JSON, nullable=False, default={"a": 1}),  # default не выразить в SQL
    )
    # legacy — есть в базе, нет в модели
    alters = _count_alters(tmp_engine)

    applied = await _start(tmp_engine, md)

    assert applied == [] and alters == []
    messages = [r.getMessage() for r in _problems(caplog)]
    for column in ("title", "label", "created", "rule", "code", "payload", "legacy"):
        assert any(f"колонка {column}" in m for m in messages), column
    assert all("make db-reset" in m for m in messages)
    assert set(await _columns(tmp_engine, "items")) == {"id", "title", "label", "legacy"}
    async with tmp_engine.connect() as conn:
        row = (await conn.execute(text("SELECT id, title, label, legacy FROM items"))).one()
    assert tuple(row) == (1, 7, "метка", "старое")


async def test_schema_check_failure_does_not_break_start(tmp_engine, caplog, monkeypatch):
    def broken(conn, tables):
        raise RuntimeError("не прочитать схему")

    monkeypatch.setattr(db, "_read_sqlite_columns", broken)
    caplog.set_level(logging.ERROR, logger=LOGGER)

    assert await _start(tmp_engine, _items_metadata()) == []
    assert any(r.exc_info and "не прочитать схему" in str(r.exc_info[1]) for r in caplog.records)


# --- Сравнение типов по affinity ---------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "affinity"),
    [
        ("INTEGER", "INTEGER"),
        ("BIGINT", "INTEGER"),
        ("VARCHAR(20)", "TEXT"),
        ("TEXT", "TEXT"),
        ("DATETIME", "NUMERIC"),
        ("TIMESTAMP", "NUMERIC"),
        ("BOOLEAN", "NUMERIC"),
        ("JSON", "NUMERIC"),
        ("FLOAT", "REAL"),
        ("", "BLOB"),
    ],
)
def test_sqlite_affinity(declared, affinity):
    assert sqlite_affinity(declared) == affinity


def test_same_affinity_is_not_drift():
    """База, созданная старыми версиями моделей, не должна давать ложных тревог."""
    md = MetaData()
    Table(
        "items",
        md,
        Column("id", Integer, primary_key=True),
        Column("title", String(32)),
        Column("stamp", DateTime(timezone=True)),
        Column("flag", Boolean),
        Column("data", JSON),
        Column("kind", Enum("a", "b", name="kind")),
        Column("big", Integer),
    )
    existing = {
        "items": {
            c.name: c
            for c in [
                DbColumn("id", "INTEGER", notnull=False, pk=True),
                DbColumn("title", "VARCHAR(20)", notnull=False, pk=False),
                DbColumn("stamp", "TIMESTAMP", notnull=False, pk=False),
                DbColumn("flag", "BOOLEAN", notnull=False, pk=False),
                DbColumn("data", "JSON", notnull=False, pk=False),
                DbColumn("kind", "VARCHAR(1)", notnull=False, pk=False),
                DbColumn("big", "BIGINT", notnull=False, pk=False),
            ]
        }
    }

    plan = plan_schema_changes(existing, md, sqlite.dialect())

    assert plan.add == [] and plan.problems == []


def test_db_nullable_model_not_null_is_not_drift():
    """Обратное направление nullability (в базе NULL разрешён, в модели нет) не тревожит."""
    md = MetaData()
    Table(
        "items",
        md,
        Column("id", Integer, primary_key=True),
        Column("title", String(20), nullable=False),
    )
    existing = {
        "items": {
            "id": DbColumn("id", "INTEGER", notnull=False, pk=True),
            "title": DbColumn("title", "VARCHAR(20)", notnull=False, pk=False),
        }
    }
    assert plan_schema_changes(existing, md, sqlite.dialect()).problems == []


# --- Настоящие модели --------------------------------------------------------------------


async def test_real_models_fresh_db_twice_no_alter_no_alarms(tmp_engine, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    alters = _count_alters(tmp_engine)

    await init_db(tmp_engine)
    await init_db(tmp_engine)

    assert alters == []
    assert not [r for r in caplog.records if r.name == LOGGER]


# --- Чистка processed_updates ------------------------------------------------------------


async def _add_keys(**ages: timedelta) -> None:
    now = _now()
    async with SessionLocal() as session:
        for key, age in ages.items():
            session.add(ProcessedUpdate(key=key, created_at=now - age))
        await session.commit()


async def _keys() -> set[str]:
    async with SessionLocal() as session:
        return set((await session.scalars(select(ProcessedUpdate.key))).all())


async def test_purge_removes_keys_older_than_a_day():
    await _add_keys(
        old=timedelta(hours=25),
        week=timedelta(days=7),
        recent=timedelta(hours=23),
        fresh=timedelta(),
    )

    assert await purge_processed_updates() == 2
    assert await _keys() == {"recent", "fresh"}


async def test_purge_respects_max_age():
    await _add_keys(two_hours=timedelta(hours=2), minute=timedelta(minutes=1))

    assert await purge_processed_updates(max_age=timedelta(hours=1)) == 1
    assert await _keys() == {"minute"}


async def test_lifespan_purges_old_keys(monkeypatch):
    await _add_keys(old=timedelta(days=2), fresh=timedelta(minutes=5))
    monkeypatch.setattr(
        main, "get_settings", lambda: Settings(_env_file=None, scheduler_enabled=False)
    )

    async with main.lifespan(main.app):
        assert await _keys() == {"fresh"}


async def test_lifespan_survives_failed_purge(monkeypatch, caplog):
    async def broken():
        raise RuntimeError("чистка сломалась")

    monkeypatch.setattr(main, "purge_processed_updates", broken)
    monkeypatch.setattr(
        main, "get_settings", lambda: Settings(_env_file=None, scheduler_enabled=False)
    )
    caplog.set_level(logging.ERROR, logger="app.main")

    async with main.lifespan(main.app):
        pass

    assert any(r.exc_info and "чистка сломалась" in str(r.exc_info[1]) for r in caplog.records)
