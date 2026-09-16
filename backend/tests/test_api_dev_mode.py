"""Dev-режим проверки initData: заголовок X-Max-Init-Data: dev вместо настоящей подписи.

Режим включается только явным флагом ALLOW_DEV_INITDATA и только при APP_ENV=dev.
Наличие или отсутствие MAX_BOT_TOKEN на него не влияет (issue #8).
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import Settings
from app.main import app

DEV_HEADERS = {"X-Max-Init-Data": "dev"}


@pytest.fixture
def with_settings(monkeypatch):
    """Подменяет настройки, которые видит current_launch, не трогая окружение."""

    def use(**overrides) -> Settings:
        values = {
            "app_env": "dev",
            "allow_dev_initdata": False,
            # токен заполнен: dev-режим не должен зависеть от него
            "max_bot_token": "real-token",
        }
        values.update(overrides)
        settings = Settings(_env_file=None, **values)
        monkeypatch.setattr(deps, "get_settings", lambda: settings)
        return settings

    return use


def test_dev_mode_off_by_default(with_settings):
    """По умолчанию флага нет — заголовок "dev" не проходит проверку подписи."""
    with_settings()
    with TestClient(app) as client:
        r = client.get("/api/me", headers=DEV_HEADERS)
    assert r.status_code == 401


def test_dev_mode_on_with_filled_token(with_settings):
    """Флаг включён и токен заполнен — /api/me отдаёт фиктивного пользователя."""
    with_settings(allow_dev_initdata=True)
    with TestClient(app) as client:
        r = client.get("/api/me", headers=DEV_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["is_dev"] is True
    assert body["user_id"] == 1


def test_dev_mode_ignored_in_prod(with_settings):
    """При APP_ENV=prod флаг не срабатывает даже если его включили."""
    with_settings(app_env="prod", allow_dev_initdata=True)
    with TestClient(app) as client:
        r = client.get("/api/me", headers=DEV_HEADERS)
    assert r.status_code == 401


def test_dev_mode_does_not_accept_other_init_data(with_settings):
    """Флаг открывает только заголовок "dev", мусорную подпись он не пропускает."""
    with_settings(allow_dev_initdata=True)
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": "user=%7B%7D&hash=deadbeef"})
    assert r.status_code == 401


def test_dev_mode_logs_warning(with_settings, caplog):
    """Режим шумит в логах: его нельзя случайно не заметить."""
    with_settings(allow_dev_initdata=True)
    with caplog.at_level(logging.WARNING, logger="app.api.deps"):
        with TestClient(app) as client:
            r = client.get("/api/me", headers=DEV_HEADERS)
    assert r.status_code == 200
    assert any(
        rec.levelno == logging.WARNING and "ALLOW_DEV_INITDATA" in rec.message
        for rec in caplog.records
    )


def test_flag_is_read_from_env(monkeypatch):
    """ALLOW_DEV_INITDATA действительно читается из окружения и выключен по умолчанию."""
    monkeypatch.delenv("ALLOW_DEV_INITDATA", raising=False)
    assert Settings(_env_file=None).allow_dev_initdata is False
    monkeypatch.setenv("ALLOW_DEV_INITDATA", "true")
    assert Settings(_env_file=None).allow_dev_initdata is True
