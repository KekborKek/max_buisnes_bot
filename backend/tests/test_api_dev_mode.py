"""Dev-режим проверки initData: заголовок X-Max-Init-Data: dev вместо настоящей подписи.

Режим включается только явным флагом ALLOW_DEV_INITDATA и только при APP_ENV=dev.
Наличие или отсутствие MAX_BOT_TOKEN на него не влияет (issue #8).
"""

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import deps
from app.core.config import Settings
from app.main import app

DEV_HEADERS = {"X-Max-Init-Data": "dev"}
TOKEN = "real-token"


def sign(params: dict, token: str = TOKEN) -> str:
    """Настоящая подпись initData — как её собирает MAX."""
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": h})


@pytest.fixture
def with_settings(monkeypatch):
    """Подменяет настройки, которые видит current_launch, не трогая окружение."""

    def use(**overrides) -> Settings:
        values = {
            "app_env": "dev",
            "allow_dev_initdata": False,
            # токен заполнен: dev-режим не должен зависеть от него
            "max_bot_token": TOKEN,
        }
        values.update(overrides)
        settings = Settings(_env_file=None, **values)
        monkeypatch.setattr(deps, "get_settings", lambda: settings)
        return settings

    return use


@pytest.fixture(autouse=True)
def reset_warning_cache():
    """Warning пишется один раз на процесс — между тестами сбрасываем счётчик."""
    deps._warn_dev_initdata_once.cache_clear()
    yield
    deps._warn_dev_initdata_once.cache_clear()


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


def test_dev_mode_on_with_empty_token(with_settings):
    """Пустой токен ничего не меняет: решает только флаг."""
    with_settings(allow_dev_initdata=True, max_bot_token="")
    with TestClient(app) as client:
        r = client.get("/api/me", headers=DEV_HEADERS)
    assert r.status_code == 200
    assert r.json()["is_dev"] is True


def test_settings_reject_dev_initdata_in_prod():
    """APP_ENV=prod с включённым флагом — приложение не поднимется."""
    with pytest.raises(ValidationError, match="ALLOW_DEV_INITDATA"):
        Settings(_env_file=None, app_env="prod", allow_dev_initdata=True)


def test_dev_mode_ignored_in_prod(monkeypatch):
    """Даже если такие настройки как-то возникли, в проде dev-ветка не срабатывает."""
    settings = Settings.model_construct(
        app_env="prod",
        allow_dev_initdata=True,
        max_bot_token=TOKEN,
        initdata_max_age_seconds=86400,
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    with TestClient(app) as client:
        r = client.get("/api/me", headers=DEV_HEADERS)
    assert r.status_code == 401


def test_dev_mode_does_not_accept_other_init_data(with_settings):
    """Флаг открывает только заголовок "dev", мусорную подпись он не пропускает."""
    with_settings(allow_dev_initdata=True)
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": "user=%7B%7D&hash=deadbeef"})
    assert r.status_code == 401


def test_real_init_data_still_works_with_flag_on(with_settings):
    """Включённый флаг не перехватывает нормальную проверку подписи."""
    with_settings(allow_dev_initdata=True)
    raw = sign({"auth_date": str(int(time.time())), "user": '{"user_id": 42, "first_name": "A"}'})
    with TestClient(app) as client:
        r = client.get("/api/me", headers={"X-Max-Init-Data": raw})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 42
    assert body["is_dev"] is False


def test_dev_mode_logs_warning(with_settings, caplog):
    """Режим шумит в логах: его нельзя случайно не заметить."""
    with_settings(allow_dev_initdata=True)
    with caplog.at_level(logging.WARNING, logger="app.api.deps"):
        with TestClient(app) as client:
            r = client.get("/api/me", headers=DEV_HEADERS)
            assert r.status_code == 200
            # второй запрос: предупреждение не повторяется на каждый вызов
            client.get("/api/me", headers=DEV_HEADERS)
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "ALLOW_DEV_INITDATA" in rec.getMessage()
    ]
    assert len(warnings) == 1


def test_flag_is_read_from_env(monkeypatch):
    """ALLOW_DEV_INITDATA действительно читается из окружения и выключен по умолчанию."""
    monkeypatch.delenv("ALLOW_DEV_INITDATA", raising=False)
    assert Settings(_env_file=None).allow_dev_initdata is False
    monkeypatch.setenv("ALLOW_DEV_INITDATA", "true")
    assert Settings(_env_file=None).allow_dev_initdata is True
