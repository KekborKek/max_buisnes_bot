"""Контракт /api/me и /api/events в части start_param (issue #12).

start_param — payload диплинка `https://max.ru/<botName>?start=<payload>`.
Он приходит внутри подписанной initData, и только подписанному значению можно верить.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import deps
from app.core.config import Settings
from app.main import app

TOKEN = "test-token"
USER = '{"user_id": 42, "first_name": "A"}'


def sign(params: dict, token: str = TOKEN) -> str:
    """Собирает подписанную initData так же, как её собирает MAX."""
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": h})


def init_data(**extra: str) -> str:
    return sign({"auth_date": str(int(time.time())), "user": USER, **extra})


@pytest.fixture
def settings(monkeypatch):
    """Настройки, которые видит current_launch. По умолчанию dev-режим выключен."""

    def use(**overrides) -> Settings:
        values = {"app_env": "dev", "allow_dev_initdata": False, "max_bot_token": TOKEN}
        values.update(overrides)
        s = Settings(_env_file=None, **values)
        monkeypatch.setattr(deps, "get_settings", lambda: s)
        return s

    return use


@pytest.fixture(autouse=True)
def reset_warning_cache():
    deps._warn_dev_initdata_once.cache_clear()
    yield
    deps._warn_dev_initdata_once.cache_clear()


async def stored_events() -> list:
    """Читает записанные события отдельной сессией — API коммитит свою."""
    from app.core.db import SessionLocal
    from app.core.models import Event

    async with SessionLocal() as session:
        rows = await session.execute(select(Event).order_by(Event.id))
        return list(rows.scalars())


def test_me_returns_start_param_from_signed_init_data(settings):
    """Подписанный start_param доезжает до мини-аппа как есть."""
    settings()
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": init_data(start_param="promo-42")})
    assert r.status_code == 200
    assert r.json()["start_param"] == "promo-42"


def test_me_without_start_param_returns_null(settings):
    """Обычный вход без диплинка: поле есть в ответе и равно null."""
    settings()
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": init_data()})
    assert r.status_code == 200
    body = r.json()
    assert body["start_param"] is None
    # обратная совместимость: прежние поля на месте
    assert body["user_id"] == 42
    assert body["first_name"] == "A"
    assert body["is_dev"] is False


def test_me_empty_start_param_is_null(settings):
    """Пустой payload в диплинке — это отсутствие payload, а не пустая строка."""
    settings()
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": init_data(start_param="")})
    assert r.status_code == 200
    assert r.json()["start_param"] is None


def test_query_start_param_ignored_for_real_init_data(settings):
    """С настоящей подписью query-параметр не может переопределить подписанное значение."""
    settings()
    with TestClient(app) as client:
        r = client.get(
            "/api/me",
            params={"start_param": "attacker"},
            headers={"X-Max-Init-Data": init_data(start_param="promo-42")},
        )
    assert r.status_code == 200
    assert r.json()["start_param"] == "promo-42"


def test_query_start_param_ignored_without_dev_mode(settings):
    """Без dev-режима подставить start_param запросом нельзя — вход только 401."""
    settings()
    with TestClient(app) as client:
        r = client.get(
            "/api/me", params={"start_param": "promo-42"}, headers={"X-Max-Init-Data": "dev"}
        )
    assert r.status_code == 401


def test_query_start_param_works_in_dev_mode(settings):
    """В dev-режиме query-параметр подменяет start_param: иначе диплинк не отладить вне MAX."""
    settings(allow_dev_initdata=True)
    with TestClient(app) as client:
        r = client.get(
            "/api/me", params={"start_param": "promo-42"}, headers={"X-Max-Init-Data": "dev"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["is_dev"] is True
    assert body["start_param"] == "promo-42"


def test_dev_mode_without_query_start_param_is_null(settings):
    """Dev-режим сам по себе start_param не выдумывает."""
    settings(allow_dev_initdata=True)
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": "dev"})
    assert r.status_code == 200
    assert r.json()["start_param"] is None


async def test_opened_event_gets_start_param(settings):
    """miniapp_opened записывается со start_param — по нему считается конверсия из QR."""
    settings()
    with TestClient(app) as client:
        r = client.post(
            "/api/events",
            json={"name": "miniapp_opened", "props": {"source": "qr"}},
            headers={"X-Max-Init-Data": init_data(start_param="promo-42")},
        )
    assert r.status_code == 204
    events = await stored_events()
    assert len(events) == 1
    assert events[0].name == "miniapp_opened"
    assert events[0].props == {"source": "qr", "start_param": "promo-42"}


async def test_opened_event_without_start_param(settings):
    """Без диплинка поле всё равно есть — колонка в выгрузке не разъезжается."""
    settings()
    with TestClient(app) as client:
        r = client.post(
            "/api/events",
            json={"name": "miniapp_opened"},
            headers={"X-Max-Init-Data": init_data()},
        )
    assert r.status_code == 204
    events = await stored_events()
    assert events[0].props == {"start_param": None}


async def test_opened_event_client_value_does_not_override_signed(settings):
    """Значение от клиента не перебивает подписанное."""
    settings()
    with TestClient(app) as client:
        r = client.post(
            "/api/events",
            json={"name": "miniapp_opened", "props": {"start_param": "attacker"}},
            headers={"X-Max-Init-Data": init_data(start_param="promo-42")},
        )
    assert r.status_code == 204
    events = await stored_events()
    assert events[0].props["start_param"] == "promo-42"


async def test_other_events_are_not_enriched(settings):
    """Остальные события не обрастают чужим полем."""
    settings()
    with TestClient(app) as client:
        r = client.post(
            "/api/events",
            json={"name": "primary_action_clicked", "props": {"x": 1}},
            headers={"X-Max-Init-Data": init_data(start_param="promo-42")},
        )
    assert r.status_code == 204
    events = await stored_events()
    assert events[0].props == {"x": 1}


def test_start_param_survives_url_encoding(settings):
    """Payload с не-ASCII и спецсимволами не портится при разборе initData."""
    settings()
    payload = "акция/42 +1"
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": init_data(start_param=payload)})
    assert r.status_code == 200
    assert r.json()["start_param"] == payload


def test_me_response_schema_has_start_param():
    """Контракт: поле объявлено в схеме и необязательно (обратная совместимость)."""
    schema = app.openapi()["components"]["schemas"]["MeResponse"]
    assert "start_param" in schema["properties"]
    assert "start_param" not in schema.get("required", [])
    assert json.dumps(schema)  # схема сериализуема — export_openapi не упадёт
