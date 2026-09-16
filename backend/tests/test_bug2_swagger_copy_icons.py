"""Баг №2: в панели с API (Swagger UI на /docs) иконки копирования наслаиваются.

Причина, которую фиксируют тесты: `/docs` отдаётся дефолтным обработчиком FastAPI
(app/main.py:25 создаёт FastAPI без своего docs-роута), а он подключает ассеты Swagger UI
по «плавающим» ссылкам `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/...`
(fastapi/openapi/docs.py, аргументы swagger_js_url / swagger_css_url по умолчанию).
Мажорный тег `@5` каждый раз резолвится в свежий патч независимо для CSS и для JS,
поэтому разметку рисует одна версия, а `position: absolute` для `.copy-to-clipboard`
и `.download-contents` приходит из другой — кнопки копирования съезжают друг на друга.

Инвариант: версии ассетов Swagger UI должны быть запиньованы (или ассеты self-hosted),
CSS и JS — из одной и той же версии. Сейчас оба теста падают.
"""

import re

from fastapi.testclient import TestClient

from app.main import app

FLOATING = re.compile(r"swagger-ui-dist@(\d+|latest|next)(?=[/@])")
PINNED = re.compile(r"swagger-ui-dist@(\d+\.\d+\.\d+)/")
ASSET_URL = re.compile(r'(?:href|src)="([^"]*swagger-ui[^"]*)"')


def _docs_html() -> str:
    with TestClient(app) as client:
        r = client.get("/docs")
    assert r.status_code == 200, f"/docs недоступен: {r.status_code}"
    return r.text


def test_docs_page_is_served():
    """Санити: панель с API действительно отдаётся и это Swagger UI."""
    html = _docs_html()
    assert "SwaggerUIBundle" in html
    assert 'id="swagger-ui"' in html


def test_swagger_assets_are_version_pinned():
    """Ассеты Swagger UI не должны подключаться по плавающему мажорному тегу."""
    html = _docs_html()
    floating = FLOATING.findall(html)
    assert not floating, (
        "В HTML /docs ассеты Swagger UI подключены по плавающей версии "
        f"swagger-ui-dist@{floating}: "
        f"{ASSET_URL.findall(html)}. "
        "Версию нужно запиньовать целиком (swagger-ui-dist@5.32.15) или раздавать ассеты "
        "со своего домена: иначе CSS и JS приезжают из разных релизов и кнопки "
        ".copy-to-clipboard / .download-contents (обе position: absolute, bottom: 10px) "
        "наслаиваются."
    )


def test_swagger_css_and_js_come_from_same_version():
    """CSS и JS Swagger UI должны быть из одной версии, иначе ломается вёрстка кнопок."""
    html = _docs_html()
    versions = set(PINNED.findall(html))
    urls = ASSET_URL.findall(html)
    assert len(versions) == 1, (
        "Не удалось подтвердить единую версию Swagger UI для CSS и JS. "
        f"Найденные ссылки: {urls}; распознанные точные версии: {sorted(versions)}. "
        "Ожидается, что swagger-ui.css и swagger-ui-bundle.js указывают на одну и ту же "
        "версию (или на локальные статические файлы)."
    )
