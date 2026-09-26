"""Подключение к базе данных (SQLAlchemy async) и её жизненный цикл: лёгкая миграция схемы
при старте и чистка ключей идемпотентности. Решение и правила — docs/decisions.md (27.09.2026)."""

import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Dialect,
    MetaData,
    UniqueConstraint,
    delete,
    event,
    literal,
    text,
)
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# timeout — сколько секунд драйвер ждёт освобождения блокировки, прежде чем бросить
# "database is locked". По умолчанию 5 секунд, нам мало: фоновая обработка апдейтов
# и мини-приложение пишут в базу одновременно.
SQLITE_TIMEOUT_SECONDS = 30


def _is_memory_db(url: Any) -> bool:
    return url.database in (None, "", ":memory:")


def _install_sqlite_pragmas(engine: AsyncEngine, url: Any) -> None:
    """PRAGMA живут в рамках соединения, поэтому выставляем их на каждом.

    WAL разводит читателей и писателя: без него SQLite берёт эксклюзивную блокировку
    на запись, и параллельные писатели падают с "database is locked".
    """
    in_memory = _is_memory_db(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            if not in_memory:
                # для :memory: WAL неприменим — база живёт в памяти процесса
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_TIMEOUT_SECONDS * 1000}")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_engine(database_url: str) -> AsyncEngine:
    """Движок с настройками конкурентности. Отдельная функция, чтобы тесты
    могли поднять такой же движок на своей базе."""
    url = make_url(database_url)
    is_sqlite = url.get_backend_name() == "sqlite"

    if is_sqlite and not _is_memory_db(url):
        # для SQLite создаём папку под файл базы
        Path(url.database or "").parent.mkdir(parents=True, exist_ok=True)

    connect_args: dict[str, Any] = {"timeout": SQLITE_TIMEOUT_SECONDS} if is_sqlite else {}
    engine = create_async_engine(database_url, connect_args=connect_args)
    if is_sqlite:
        _install_sqlite_pragmas(engine, url)
    return engine


engine = create_engine(get_settings().database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# --- Лёгкая миграция схемы -----------------------------------------------------------------
# create_all создаёт только недостающие таблицы и никогда не трогает существующие. Поэтому после
# него сверяем колонки: безопасно добавляемые — ALTER TABLE ADD COLUMN, остальное — log.error.
# Колонки никогда не удаляются и не переименовываются автоматически.

SCHEMA_FIX_HINT = (
    "Автоматически не исправляется, данные не тронуты. Локально: make db-reset. "
    "На проде базу не сносить: приведите модель к правилу (новые колонки — nullable или "
    "с default, колонки не удалять и не переименовывать) либо поправьте базу вручную "
    "(docs/decisions.md, 27.09.2026)."
)


@dataclass(frozen=True)
class DbColumn:
    """Колонка, как она есть в файле SQLite (PRAGMA table_info)."""

    name: str
    declared_type: str
    notnull: bool
    pk: bool


@dataclass(frozen=True)
class AddColumn:
    table: str
    column: str
    ddl: str


@dataclass
class SchemaPlan:
    add: list[AddColumn] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def sqlite_affinity(declared_type: str) -> str:
    """Type affinity SQLite по объявленному типу (https://sqlite.org/datatype3.html, §3.1).

    Сравниваем именно её, а не строку типа: VARCHAR(20) и VARCHAR(32), DATETIME и TIMESTAMP,
    BOOLEAN и JSON хранятся одинаково, и расхождение в строке — не повод для тревоги.
    """
    t = declared_type.upper()
    if "INT" in t:
        return "INTEGER"
    if "CHAR" in t or "CLOB" in t or "TEXT" in t:
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "REAL"
    return "NUMERIC"


def _default_sql(col: Column, dialect: Dialect) -> str | None:
    """SQL-выражение DEFAULT для ADD COLUMN или None, если default нет или он не скалярный.

    ValueError — default есть, но выразить его в SQL нельзя (например, dict для JSON).
    """
    if col.server_default is not None:
        return dialect.ddl_compiler(dialect, None).get_column_default_string(col)
    default = col.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    try:
        return str(
            literal(default.arg, col.type).compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        )
    except Exception as exc:
        raise ValueError(f"default {default.arg!r} нельзя записать в SQL") from exc


def _add_column_ddl(col: Column, dialect: Dialect) -> str:
    """ALTER TABLE … ADD COLUMN или ValueError с причиной, почему добавить нельзя."""
    if col.primary_key:
        raise ValueError("колонка входит в первичный ключ")
    table = col.table
    if col.unique or any(
        isinstance(c, UniqueConstraint) and col.name in c.columns for c in table.constraints
    ):
        raise ValueError("колонка с ограничением UNIQUE")
    if any(index.unique and col.name in index.columns for index in table.indexes):
        # ALTER без индекса оставил бы колонку без уникальности, а сбой создания индекса
        # после ALTER не откатить: DDL в pysqlite идёт в autocommit
        raise ValueError("колонка входит в уникальный индекс")
    default_sql = _default_sql(col, dialect)
    if not col.nullable and default_sql is None:
        raise ValueError("новая NOT NULL колонка без скалярного или серверного default")

    quote = dialect.identifier_preparer.quote
    parts = [
        f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(col.name)}",
        col.type.compile(dialect=dialect),
    ]
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    if not col.nullable:
        parts.append("NOT NULL")
    fks = list(col.foreign_keys)
    if len(fks) > 1:
        raise ValueError("колонка ссылается на несколько таблиц")
    if fks:
        target = fks[0].column
        parts.append(f"REFERENCES {quote(target.table.name)} ({quote(target.name)})")
        if fks[0].ondelete:
            parts.append(f"ON DELETE {fks[0].ondelete}")
    return " ".join(parts)


def plan_schema_changes(
    existing: Mapping[str, Mapping[str, DbColumn]], metadata: MetaData, dialect: Dialect
) -> SchemaPlan:
    """Сравнивает колонки таблиц из базы (`existing`) с моделями. Ничего не выполняет.

    Смотрит только на таблицы из `metadata`, которые уже есть в базе: недостающие создаёт
    create_all, чужие таблицы не наши.
    """
    plan = SchemaPlan()
    for table in metadata.sorted_tables:
        db_columns = existing.get(table.name)
        if db_columns is None:
            continue
        for col in table.columns:
            where = f"таблица {table.name}, колонка {col.name}"
            db_col = db_columns.get(col.name)
            if db_col is None:
                try:
                    plan.add.append(AddColumn(table.name, col.name, _add_column_ddl(col, dialect)))
                except ValueError as exc:
                    plan.problems.append(f"{where}: нет в базе, добавить нельзя — {exc}")
                continue
            model_type = col.type.compile(dialect=dialect)
            if sqlite_affinity(model_type) != sqlite_affinity(db_col.declared_type):
                plan.problems.append(
                    f"{where}: в базе тип {db_col.declared_type or '(пусто)'}, "
                    f"в модели {model_type}"
                )
            if db_col.notnull and col.nullable and not col.primary_key:
                plan.problems.append(
                    f"{where}: в базе NOT NULL, в модели nullable — запись NULL упадёт"
                )
        for name in db_columns.keys() - set(table.columns.keys()):
            plan.problems.append(f"таблица {table.name}, колонка {name}: есть в базе, нет в модели")
    return plan


def _read_sqlite_columns(conn: Connection, tables: list[str]) -> dict[str, dict[str, DbColumn]]:
    """Колонки существующих таблиц. PRAGMA, а не inspector: нужен объявленный тип как есть."""
    result: dict[str, dict[str, DbColumn]] = {}
    present = set(
        conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).scalars()
    )
    for name in tables:
        if name not in present:
            continue
        quoted = conn.dialect.identifier_preparer.quote(name)
        rows = conn.execute(text(f"PRAGMA table_info({quoted})")).mappings()
        result[name] = {
            r["name"]: DbColumn(r["name"], r["type"] or "", bool(r["notnull"]), bool(r["pk"]))
            for r in rows
        }
    return result


async def sync_schema(target: AsyncEngine, metadata: MetaData) -> list[str]:
    """Добавляет безопасные недостающие колонки, о прочих расхождениях пишет log.error.

    Возвращает выполненные ALTER. Не бросает исключений: старт приложения важнее, а о сбое
    будет log.exception. DDL в pysqlite выполняется в autocommit: каждый ALTER применяется
    сразу и откатить его нельзя, поэтому сначала выполняются все ALTER (неудачный не мешает
    остальным), а затем отдельным проходом создаются индексы, в которые входят новые колонки,
    — в том числе составные на несколько новых колонок. Уникальные индексы сюда не попадают:
    такие колонки автоматически не добавляются (_add_column_ddl).
    """
    if target.dialect.name != "sqlite":
        log.info("Сверка схемы БД рассчитана на SQLite, для %s пропущена", target.dialect.name)
        return []
    applied: list[str] = []
    try:
        async with target.connect() as conn:
            existing = await conn.run_sync(
                _read_sqlite_columns, [t.name for t in metadata.sorted_tables]
            )
        plan = plan_schema_changes(existing, metadata, target.dialect)
        for problem in plan.problems:
            log.error("Схема БД разошлась с моделями: %s. %s", problem, SCHEMA_FIX_HINT)

        added: dict[str, set[str]] = {}
        for change in plan.add:
            try:
                async with target.begin() as conn:
                    await conn.execute(text(change.ddl))
            except Exception:
                log.exception(
                    "Схема БД: не удалось добавить колонку %s.%s (%s). %s",
                    change.table,
                    change.column,
                    change.ddl,
                    SCHEMA_FIX_HINT,
                )
                continue
            applied.append(change.ddl)
            added.setdefault(change.table, set()).add(change.column)
            log.warning(
                "Схема БД: в таблицу %s добавлена колонка %s (%s)",
                change.table,
                change.column,
                change.ddl,
            )

        for table_name, columns in added.items():
            for index in metadata.tables[table_name].indexes:
                if not columns & set(index.columns.keys()):
                    continue
                try:
                    async with target.begin() as conn:
                        await conn.run_sync(index.create, checkfirst=True)
                except Exception:
                    log.exception(
                        "Схема БД: колонки добавлены, но не удалось создать индекс %s "
                        "на таблице %s. Создайте его вручную. %s",
                        index.name,
                        table_name,
                        SCHEMA_FIX_HINT,
                    )
                    continue
                log.warning("Схема БД: на таблице %s создан индекс %s", table_name, index.name)
    except Exception:
        log.exception("Сверка схемы БД не удалась, приложение продолжает работу")
    return applied


async def init_db(target: AsyncEngine | None = None) -> None:
    """Создаёт недостающие таблицы и досоздаёт безопасные колонки (лёгкая миграция, без Alembic).

    Правило для правок моделей: новые колонки — только nullable или со скалярным/серверным
    default; удалять и переименовывать колонки нельзя (docs/decisions.md, 27.09.2026).
    """
    from app.core import models  # noqa: F401  регистрирует модели

    target = target or engine
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await sync_schema(target, Base.metadata)


# --- Чистка ключей идемпотентности ---------------------------------------------------------
# MAX повторяет доставку в пределах минут, поэтому ключи старше суток не нужны.
PROCESSED_UPDATES_TTL = timedelta(hours=24)


async def purge_processed_updates(
    now: datetime | None = None, max_age: timedelta = PROCESSED_UPDATES_TTL
) -> int:
    """Удаляет ключи обработанных апдейтов старше `max_age`. Возвращает число удалённых.

    `created_at` в SQLite хранится строкой без смещения (UTC, как пишет utcnow), поэтому
    порог приводим к UTC: иначе aware-время в другом поясе сравнивалось бы по местным часам.
    Индекса по `created_at` нет — это полный скан таблицы раз в старт, для неё это дёшево.
    """
    from app.core.models import ProcessedUpdate

    cutoff = (now or datetime.now(UTC)).astimezone(UTC) - max_age
    async with SessionLocal() as session:
        result = await session.execute(
            delete(ProcessedUpdate).where(ProcessedUpdate.created_at < cutoff)
        )
        await session.commit()
    return result.rowcount or 0


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
