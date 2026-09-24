"""LEAD-9 (#21): падение обработчика должно быть разборчиво в логе — ключ апдейта и user_id,
а не только тип апдейта (`dispatcher.py`, except в `process_update`). Без этого на демо
невозможно понять, у какого пользователя и на каком апдейте упало.

Обработчик подменяется на уровне `router.resolve`, а не через реальные `bot/handlers/*` —
этот файл принадлежит только dispatcher.py, реальные обработчики трогать нельзя (issue #21,
раздел «Можно менять»).
"""

import logging

from app.bot.context import Ctx
from app.bot.dispatcher import process_update, update_key
from app.bot.router import router
from tests.conftest import load_update


async def _broken_handler(ctx: Ctx) -> None:
    raise RuntimeError("обработчик сломан")


async def test_handler_failure_logs_update_key_and_user_id(fake_max, monkeypatch, caplog):
    async def resolve_to_broken_handler(ctx: Ctx):
        return _broken_handler

    monkeypatch.setattr(router, "resolve", resolve_to_broken_handler)

    update = load_update("bot_started")
    key = update_key(update)
    user_id = update["user"]["user_id"]

    caplog.set_level(logging.INFO)
    await process_update(update, fake_max)

    error_records = [
        r for r in caplog.records if r.name == "app.bot.dispatcher" and r.levelname == "ERROR"
    ]
    assert error_records, (
        "не нашли ERROR-запись от dispatcher — падение обработчика не залогировано"
    )

    message = error_records[0].getMessage()
    assert key is not None and key in message, f"в логе нет ключа апдейта {key!r}: {message!r}"
    assert str(user_id) in message, f"в логе нет user_id {user_id}: {message!r}"


async def test_handler_failure_still_replies_with_fallback(fake_max, monkeypatch):
    """Побочная проверка: диагностика логом не должна сломать штатный путь «Повторить»."""

    async def resolve_to_broken_handler(ctx: Ctx):
        return _broken_handler

    monkeypatch.setattr(router, "resolve", resolve_to_broken_handler)

    update = load_update("bot_started")
    await process_update(update, fake_max)

    assert fake_max.sent, "после падения обработчика пользователь должен получить ответ"
