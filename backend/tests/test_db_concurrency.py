"""Параллельная запись в SQLite: WAL и busy_timeout вместо "database is locked".

Вебхук отвечает 200 и запускает process_update в фоне, мини-приложение пишет события
через API — писателей несколько одновременно. Без WAL и busy_timeout такой сценарий
падает ровно на демо, а на одном пользователе не воспроизводится.

Проверки идут на свежем файле базы во временной папке: journal_mode записан внутрь
файла навсегда, и на переиспользуемой data/test.db тест был бы зелёным даже без фикса.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.bot.dispatcher import process_update
from app.core.db import SessionLocal, create_engine
from app.core.models import Event
from app.core.texts import t
from tests.conftest import load_update

PARALLEL_UPDATES = 25
BUSY_TIMEOUT_MS = 30000
# короткий таймаут: без WAL писатель должен упереться в блокировку сразу, а не ждать
LOCK_PROBE_TIMEOUT_SECONDS = 0.2


def _update_for(user_id: int) -> dict:
    """Апдейт bot_started от отдельного пользователя: свой ключ идемпотентности."""
    update = load_update("bot_started")
    update["user"] = dict(update["user"], user_id=user_id)
    update["chat_id"] = user_id
    return update


async def _prepared_db_file(tmp_path: Path) -> Path:
    """Создаёт файл базы через наш движок — так же, как это делает приложение."""
    db_file = tmp_path / "app.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_file}")
    try:
        async with engine.connect():
            pass
    finally:
        await engine.dispose()
    return db_file


async def test_pragmas_applied_on_every_connection(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    try:
        # два соединения сразу: PRAGMA должны стоять на каждом, а не только на первом
        async with engine.connect() as first, engine.connect() as second:
            for conn in (first, second):
                assert await conn.scalar(text("PRAGMA journal_mode")) == "wal"
                assert await conn.scalar(text("PRAGMA busy_timeout")) == BUSY_TIMEOUT_MS
                assert await conn.scalar(text("PRAGMA foreign_keys")) == 1
                assert await conn.scalar(text("PRAGMA synchronous")) == 1  # NORMAL
    finally:
        await engine.dispose()


async def test_reader_does_not_block_writer(tmp_path):
    """Смысл WAL: читатель держит снимок, писатель в это время коммитит.

    Под journal_mode=delete второе соединение получает "database is locked".
    """
    db_file = await _prepared_db_file(tmp_path)

    setup = sqlite3.connect(db_file)
    setup.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    setup.commit()
    setup.close()

    reader = sqlite3.connect(db_file, timeout=LOCK_PROBE_TIMEOUT_SECONDS, isolation_level=None)
    writer = sqlite3.connect(db_file, timeout=LOCK_PROBE_TIMEOUT_SECONDS, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM probe").fetchall()  # снимок открыт и удерживается

        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO probe (id) VALUES (1)")
            writer.execute("COMMIT")
        except sqlite3.OperationalError as e:  # pragma: no cover — путь без WAL
            pytest.fail(f"писатель заблокирован читателем: {e}")
    finally:
        reader.close()
        writer.close()

    check = sqlite3.connect(db_file)
    assert check.execute("SELECT count(*) FROM probe").fetchone()[0] == 1
    check.close()


async def test_parallel_process_update_does_not_lock_database(fake_max):
    updates = [_update_for(1000 + i) for i in range(PARALLEL_UPDATES)]

    results = await asyncio.gather(
        *(process_update(u, fake_max) for u in updates), return_exceptions=True
    )

    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"параллельные апдейты упали: {errors!r}"

    # process_update глушит исключения и отвечает текстом ошибки — проверяем, что
    # все 25 пользователей получили приветствие, а не "что-то пошло не так"
    assert len(fake_max.sent) == PARALLEL_UPDATES
    assert [m["text"] for m in fake_max.sent] == [t("start.greeting")] * PARALLEL_UPDATES

    async with SessionLocal() as session:
        events = await session.scalar(
            select(func.count()).select_from(Event).where(Event.name == "bot_started")
        )
    assert events == PARALLEL_UPDATES
