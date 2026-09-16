"""Подключение к базе данных (SQLAlchemy async)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


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


async def init_db() -> None:
    """Создаёт таблицы при старте. Для хакатона миграции (Alembic) не нужны."""
    from app.core import models  # noqa: F401  регистрирует модели

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
