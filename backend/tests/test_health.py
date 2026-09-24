from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app


class _BrokenSession:
    """Подмена SessionLocal(): любой async with роняется до первого запроса к БД."""

    async def __aenter__(self):
        raise RuntimeError("база недоступна")

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "ok"
    # env/data_mode смотрят в docs/STATUS.md — оставляем как было
    assert body["env"] == "dev"
    assert "data_mode" in body


def test_health_checks_db_for_real():
    """LEAD-9 (#21): /health не должен отвечать "ok" константами — проверяем настоящий SELECT 1."""
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.json()["db"] == "ok"


def test_health_returns_503_when_db_unavailable(monkeypatch):
    """Docker HEALTHCHECK ходит в /health — с мёртвой базой контейнер не должен считаться
    здоровым (issue #21)."""
    monkeypatch.setattr(main_module, "SessionLocal", lambda: _BrokenSession())

    with TestClient(app) as client:
        r = client.get("/health")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "error"
    assert body["db"] == "error"


def test_health_reports_token_configured_without_leaking_it():
    with TestClient(app) as client:
        r = client.get("/health")
    body = r.json()
    assert body["token_configured"] is True  # conftest выставляет MAX_BOT_TOKEN=test-token
    assert "test-token" not in r.text
    assert "max_bot_token" not in body


def test_health_token_configured_false_when_token_empty(monkeypatch):
    monkeypatch.setattr(
        main_module, "get_settings", lambda: Settings(_env_file=None, max_bot_token="")
    )
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.json()["token_configured"] is False


def test_webhook_rejects_bad_secret():
    with TestClient(app) as client:
        r = client.post("/webhook/max", json={}, headers={"X-Max-Bot-Api-Secret": "wrong"})
    assert r.status_code == 403
