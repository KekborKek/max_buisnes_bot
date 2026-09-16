"""Приём апдейтов от MAX по вебхуку.

Правила MAX: ответить 200 в течение 30 с; иначе доставка считается ошибкой,
а после 8 часов без успешной доставки подписка отключается. Поэтому отвечаем сразу,
а обработку запускаем в фоне.
"""

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.bot.dispatcher import process_update
from app.core.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/max", include_in_schema=False)
async def max_webhook(
    request: Request,
    background: BackgroundTasks,
    x_max_bot_api_secret: str | None = Header(default=None),
) -> dict:
    secret = get_settings().max_webhook_secret
    if secret and not hmac.compare_digest(x_max_bot_api_secret or "", secret):
        raise HTTPException(status_code=403, detail="bad secret")
    try:
        update = await request.json()
    except ValueError:
        log.warning("webhook: invalid JSON")
        return {"ok": True}  # 200, чтобы MAX не ретраил мусор
    background.add_task(process_update, update, request.app.state.max_client)
    return {"ok": True}
