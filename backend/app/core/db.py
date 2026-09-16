"""Подключение к базе данных (SQLAlchemy async)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_url = _settings.database_url
_is_sqlite = _url.startswith("sqlite")
_db_path = _url.split("///", 1)[1] if _is_sqlite and "///" in _url else ""
_is_memory = _is_sqlite and (not _db_path or _db_path == ":memory:")

if _is_sqlite and not _is_memory:
    # для SQLite создаём папку под файл базы
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

# timeout — сколько секунд драйвер ждёт освобождения блокировки, прежде чем
# бросить "database is locked". По умолчанию 5 секунд, нам мало: вебхук и
# мини-приложение пишут в базу одновременно.
_connect_args: dict[str, Any] = {"timeout": 30} if _is_sqlite else {}

engine = create_async_engine(_settings.database_url, connect_args=_connect_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        """PRAGMA живут в рамках соединения, поэтому выставляем на каждом.

        WAL разводит читателей и писателя: без него SQLite берёт эксклюзивную
        блокировку на запись и параллельные апдейты падают с "database is locked".
        """
        cursor = dbapi_connection.cursor()
        try:
            if not _is_memory:
                # для :memory: WAL неприменим — база живёт в памяти процесса
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


async def init_db() -> None:
    """Создаёт таблицы при старте. Для хакатона миграции (Alembic) не нужны."""
    from app.core import models  # noqa: F401  регистрирует модели

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
