"""Обработка одного апдейта: идемпотентность → разбор → обработчик → общий catch ошибок."""

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import IntegrityError

import app.bot.handlers  # noqa: F401  регистрирует обработчики
from app.bot.context import Ctx
from app.bot.router import router
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.max_client import MaxClient
from app.core.models import ProcessedUpdate
from app.core.texts import t

log = logging.getLogger(__name__)


def update_key(update: dict) -> str | None:
    """Уникальный ключ апдейта для защиты от повторной доставки."""
    ut = update.get("update_type", "")
    if ut == "message_callback":
        cid = (update.get("callback") or {}).get("callback_id")
        return f"cb:{cid}" if cid else None
    mid = ((update.get("message") or {}).get("body") or {}).get("mid")
    if mid:
        return f"{ut}:{mid}"
    user_id = (update.get("user") or {}).get("user_id")
    return f"{ut}:{user_id}:{update.get('timestamp')}"


def _capture(update: dict) -> None:
    folder = Path("data/captured_updates")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{update.get('update_type', 'unknown')}.json"
    (folder / name).write_text(json.dumps(update, ensure_ascii=False, indent=2), encoding="utf-8")


async def process_update(update: dict, max_client: MaxClient) -> None:
    if get_settings().capture_updates:
        _capture(update)

    async with SessionLocal() as session:
        ctx = Ctx.from_update(update, session, max_client)
        try:
            key = update_key(update)
            if key:
                session.add(ProcessedUpdate(key=key))
                try:
                    await session.flush()
                except IntegrityError:
                    log.info("duplicate update skipped: %s", key)
                    return

            handler = await router.resolve(ctx)
            if handler:
                await handler(ctx)
            await session.commit()
        except Exception:
            log.exception("handler failed for %s", ctx.update_type)
            try:
                await session.rollback()
            except Exception:
                log.exception("rollback failed for %s", ctx.update_type)
            try:
                await ctx.reply(t("errors.internal"))
            except Exception:
                log.exception("failed to send error message")
