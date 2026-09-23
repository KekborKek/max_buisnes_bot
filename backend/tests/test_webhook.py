"""Тесты POST /webhook/max: MAX должен получать быстрый 200 даже на мусор.

Правило проекта (AGENTS.md, docs/prompts/executor.md): вебхук отвечает 200 быстро,
тяжёлая обработка — в фоне и идемпотентно. Здесь проверяем контракт самого HTTP-слоя,
а не логику process_update (она покрыта test_dispatcher_transaction.py и другими).
"""

import pytest
from fastapi.testclient import TestClient

from app import webhook as webhook_module
from app.core.config import Settings
from app.main import app

SECRET = "test-secret"


@pytest.fixture
def settings(monkeypatch):
    """Настройки, которые видит max_webhook: подменяем без изменения окружения."""

    def use(**overrides) -> Settings:
        values = {"app_env": "dev", "max_webhook_secret": SECRET}
        values.update(overrides)
        s = Settings(_env_file=None, **values)
        monkeypatch.setattr(webhook_module, "get_settings", lambda: s)
        return s

    return use


@pytest.fixture
def recorder(monkeypatch):
    """Подменяет process_update: запоминает, с каким апдейтом его вызвали, ничего не делает."""
    calls: list[dict] = []

    async def fake(update, max_client):
        calls.append(update)

    monkeypatch.setattr(webhook_module, "process_update", fake)
    return calls


def test_webhook_accepts_correct_secret_and_schedules_processing(settings, recorder):
    """Верный секрет — 200, а process_update вызван именно с распарсенным телом апдейта."""
    settings()
    update = {"update_type": "message_created", "message": {"body": {"text": "привет"}}}
    with TestClient(app) as client:
        r = client.post("/webhook/max", json=update, headers={"X-Max-Bot-Api-Secret": SECRET})
    assert r.status_code == 200
    assert recorder == [update]


def test_webhook_invalid_json_returns_200_without_processing(settings, recorder):
    """Битый JSON — 200 (иначе MAX ретраит мусор), но process_update не запускается."""
    settings()
    with TestClient(app) as client:
        r = client.post(
            "/webhook/max",
            content=b"not a json{",
            headers={
                "X-Max-Bot-Api-Secret": SECRET,
                "Content-Type": "application/json",
            },
        )
    assert r.status_code == 200
    assert recorder == []


def test_webhook_empty_secret_skips_check(settings, recorder):
    """Пустой MAX_WEBHOOK_SECRET (dev) — проверка секрета пропускается.

    Апдейт всё равно уходит в обработку. Прод-конфигурацию с пустым секретом
    отдельная задача (#35) запрещает на старте — здесь сознательно проверяем
    только dev-режим.
    """
    settings(app_env="dev", max_webhook_secret="")
    update = {"update_type": "message_created"}
    with TestClient(app) as client:
        r = client.post("/webhook/max", json=update)  # заголовка нет вовсе
    assert r.status_code == 200
    assert recorder == [update]
