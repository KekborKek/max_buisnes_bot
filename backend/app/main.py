"""Точка входа: uvicorn app.main:app"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse

from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.db import init_db
from app.core.max_client import MaxClient
from app.webhook import router as webhook_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Баг №2: дефолтный /docs у FastAPI грузит swagger-ui-dist по плавающему тегу
# @5 — CSS и JS резолвятся независимо и могут разъехаться по версиям, из-за
# чего .copy-to-clipboard и .download-contents накладываются друг на друга.
# Версия запинована целиком и совпадает для CSS и JS.
SWAGGER_UI_VERSION = "5.32.15"
SWAGGER_UI_JS_URL = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js"
)
SWAGGER_UI_CSS_URL = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.max_client = MaxClient()
    yield
    await app.state.max_client.close()


app = FastAPI(
    title="MAX Business Bot API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

if app.swagger_ui_oauth2_redirect_url:

    @app.api_route(
        app.swagger_ui_oauth2_redirect_url, methods=["GET", "HEAD"], include_in_schema=False
    )
    async def swagger_ui_redirect() -> HTMLResponse:
        return get_swagger_ui_oauth2_redirect_html()


@app.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False)
async def swagger_ui_html(req: Request) -> HTMLResponse:
    root_path = req.scope.get("root_path", "").rstrip("/")
    openapi_url = root_path + app.openapi_url
    oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url
    if oauth2_redirect_url:
        oauth2_redirect_url = root_path + oauth2_redirect_url
    return get_swagger_ui_html(
        openapi_url=openapi_url,
        title=f"{app.title} - Swagger UI",
        oauth2_redirect_url=oauth2_redirect_url,
        swagger_js_url=SWAGGER_UI_JS_URL,
        swagger_css_url=SWAGGER_UI_CSS_URL,
        init_oauth=app.swagger_ui_init_oauth,
        swagger_ui_parameters=app.swagger_ui_parameters,
    )


app.include_router(webhook_router)
app.include_router(api_router)


@app.get("/health", tags=["service"], summary="Проверка, что сервис жив")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "data_mode": s.data_mode}
