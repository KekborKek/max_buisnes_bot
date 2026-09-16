"""Точка входа: uvicorn app.main:app"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.db import init_db
from app.core.max_client import MaxClient
from app.webhook import router as webhook_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.max_client = MaxClient()
    yield
    await app.state.max_client.close()


app = FastAPI(title="MAX Business Bot API", version="0.1.0", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(api_router)


@app.get("/health", tags=["service"], summary="Проверка, что сервис жив")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "data_mode": s.data_mode}
