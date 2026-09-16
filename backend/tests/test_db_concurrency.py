"""Параллельная запись в SQLite: WAL и busy_timeout вместо "database is locked".

Вебхук отвечает 200 и запускает process_update в фоне, мини-приложение пишет
события через API — писателей несколько одновременно. Без WAL и busy_timeout
такой сценарий падает ровно на демо, а на одном пользователе не воспроизводится.
"""

import asyncio

from sqlalchemy import func, select, text

from app.bot.dispatcher import process_update
from app.core.db import SessionLocal, engine
from app.core.models import Event
from tests.conftest import load_update

PARALLEL_UPDATES = 25


def _update_for(user_id: int) -> dict:
    """Апдейт bot_started от отдельного пользователя: свой ключ идемпотентности."""
    update = load_update("bot_started")
    update["user"] = dict(update["user"], user_id=user_id)
    update["chat_id"] = user_id
    update["timestamp"] = update["timestamp"] + user_id
    return update


async def test_pragmas_applied_on_every_connection():
    async with engine.connect() as conn:
        journal_mode = await conn.scalar(text("PRAGMA journal_mode"))
        busy_timeout = await conn.scalar(text("PRAGMA busy_timeout"))
        foreign_keys = await conn.scalar(text("PRAGMA foreign_keys"))

    assert journal_mode == "wal"
    assert busy_timeout == 30000
    assert foreign_keys == 1


async def test_parallel_process_update_does_not_lock_database(fake_max):
    updates = [_update_for(1000 + i) for i in range(PARALLEL_UPDATES)]

    results = await asyncio.gather(
        *(process_update(u, fake_max) for u in updates), return_exceptions=True
    )

    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"параллельные апдейты упали: {errors!r}"

    async with SessionLocal() as session:
        events = await session.scalar(
            select(func.count()).select_from(Event).where(Event.name == "bot_started")
        )
    assert events == PARALLEL_UPDATES
    assert len(fake_max.sent) == PARALLEL_UPDATES
