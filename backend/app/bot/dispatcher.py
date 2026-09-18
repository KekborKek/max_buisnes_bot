"""Обработка одного апдейта: идемпотентность → разбор → обработчик → отправка → catch ошибок."""

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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


async def _mark_processed(session: AsyncSession, update: dict) -> bool:
    """Отмечает апдейт обработанным отдельной короткой транзакцией. False — это повтор.

    Ключ коммитится ДО вызова обработчика и остаётся, даже если обработчик упал: повторная
    доставка того же апдейта будет отброшена (at-most-once). Так выбрано осознанно — при
    детерминированной ошибке обработчика снятие ключа превратило бы повторы MAX в бесконечный
    цикл падений и сообщений об ошибке пользователю. Цена решения: апдейт, упавший на случайной
    ошибке, второго шанса не получит, пользователь увидит t("errors.internal") и повторит
    действие сам.
    """
    key = update_key(update)
    if not key:
        return True
    session.add(ProcessedUpdate(key=key))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        log.info("duplicate update skipped: %s", key)
        return False
    return True


async def _answer_callback(ctx: Ctx) -> None:
    """Гасит спиннер на кнопке. Место здесь, а не в обработчиках.

    Пока на нажатие не ответили, MAX крутит индикатор на кнопке. Когда `answer_callback`
    вызывал каждый обработчик сам, забытый вызов в новом обработчике оставлял кнопку
    «висеть» — отвечаем централизованно, до вызова обработчика.

    Ошибка ответа не прерывает обработку: обработать апдейт важнее, чем погасить индикатор.
    """
    if ctx.callback_id is None:
        return
    try:
        await ctx.max.answer_callback(ctx.callback_id)
    except Exception:
        log.exception("answer_callback failed for %s", ctx.callback_id)


async def process_update(update: dict, max_client: MaxClient) -> None:
    """Обработка апдейта: короткие транзакции, сеть — только вне них.

    Порядок: коммит ключа идемпотентности → обработчик → коммит его записей → отправка
    накопленных ответов. Отправка вынесена из транзакции через буфер в `Ctx` (`ctx.reply`
    складывает, `ctx.send_outbox` отправляет): SQLite допускает одного писателя, и пока один
    апдейт ждёт ответа MAX API (таймаут httpx 35 секунд), все остальные стояли бы за
    блокировкой записи дольше её busy_timeout (30 секунд) — то есть снова получали бы
    "database is locked".

    Буфер выбран вместо «коммит прямо перед отправкой» потому, что не требует от обработчиков
    ничего знать о транзакции: `ctx.reply` можно вызывать в любом месте обработчика, в том
    числе несколько раз и до записи в БД, и порядок сообщений сохранится.
    """
    if get_settings().capture_updates:
        _capture(update)

    async with SessionLocal() as session:
        ctx = Ctx.from_update(update, session, max_client)
        try:
            if not await _mark_processed(session, update):
                return

            # Транзакции здесь нет: ключ уже закоммичен, обработчик ещё не начал писать —
            # значит сетевой вызов не держит блокировку записи.
            await _answer_callback(ctx)

            handler = await router.resolve(ctx)
            if handler:
                await handler(ctx)
            await session.commit()
        except Exception:
            log.exception("handler failed for %s", ctx.update_type)
            ctx.drop_outbox()  # записей в БД нет — рассказывать о них пользователю нечего
            try:
                await session.rollback()
            except Exception:
                log.exception("rollback failed for %s", ctx.update_type)
            await ctx.reply(t("errors.internal"))

        await ctx.send_outbox()  # транзакция закрыта: и после commit, и после rollback
