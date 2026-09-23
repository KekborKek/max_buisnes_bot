"""Точка входа: uvicorn app.main:app"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app.api.routes import router as api_router
from app.calendar import reminders
from app.core.config import get_settings
from app.core.db import init_db
from app.core.max_client import MaxClient
from app.webhook import router as webhook_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

SCHEDULER_INTERVAL_SECONDS = 60.0

# Версия запинена целиком: дефолтный /docs у FastAPI тянет CSS и JS по
# плавающему тегу @5, и они могут резолвиться в разные релизы независимо
# друг от друга, из-за чего съезжают кнопки .copy-to-clipboard/.download-contents.
SWAGGER_UI_VERSION = "5.32.15"


async def scheduler_loop(
    max_client: MaxClient, interval: float = SCHEDULER_INTERVAL_SECONDS
) -> None:
    """Раз в `interval` секунд — reminders.tick. Работает, пока задачу не отменят.

    Упавший тик пишется в лог и не останавливает цикл: одно битое уведомление не должно
    выключать все напоминания до перезапуска. CancelledError не ловится — это штатная остановка.
    """
    while True:
        try:
            await reminders.tick(datetime.now(UTC), max_client)
        except Exception:
            log.exception("Тик планировщика напоминаний упал, цикл продолжается")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.max_client = MaxClient()
    app.state.scheduler_task = None
    if get_settings().scheduler_enabled:
        app.state.scheduler_task = asyncio.create_task(
            scheduler_loop(app.state.max_client), name="reminders-scheduler"
        )
    try:
        yield
    finally:
        task = app.state.scheduler_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await app.state.max_client.close()


app = FastAPI(title="MAX Business Bot API", version="0.1.0", lifespan=lifespan, docs_url=None)
app.include_router(webhook_router)
app.include_router(api_router)


@app.get("/docs", include_in_schema=False)
async def swagger_ui_html() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_js_url=(
            f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js"
        ),
        swagger_css_url=(
            f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css"
        ),
    )


@app.get("/health", tags=["service"], summary="Проверка, что сервис жив")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "data_mode": s.data_mode}
