"""T0 (#35): настройки календаря и защита прода от секрета из примера."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(monkeypatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", []), ("42", [42]), ("1,22, 333", [1, 22, 333]), (" 5 , ,6,", [5, 6])],
)
def test_admin_ids_parsed_from_comma_list(monkeypatch, raw, expected):
    assert _settings(monkeypatch, ADMIN_IDS=raw).admin_ids == expected


def test_admin_ids_empty_by_default(monkeypatch):
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    assert Settings(_env_file=None).admin_ids == []


def test_admin_ids_reject_garbage(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, ADMIN_IDS="1,abc")


@pytest.mark.parametrize("secret", ["", "change-me-please", "  "])
def test_prod_refuses_empty_or_example_webhook_secret(monkeypatch, secret):
    with pytest.raises(ValidationError, match="MAX_WEBHOOK_SECRET"):
        _settings(monkeypatch, APP_ENV="prod", MAX_WEBHOOK_SECRET=secret)


def test_prod_starts_with_real_secret(monkeypatch):
    s = _settings(monkeypatch, APP_ENV="prod", MAX_WEBHOOK_SECRET="Real_secret-123")
    assert s.app_env == "prod"


def test_dev_allows_example_secret(monkeypatch):
    s = _settings(monkeypatch, APP_ENV="dev", MAX_WEBHOOK_SECRET="change-me-please")
    assert s.max_webhook_secret == "change-me-please"


def test_calendar_defaults(monkeypatch):
    for key in ("SUPPORT_URL", "PRIVACY_URL", "SCHEDULER_ENABLED", "MAX_BOT_USERNAME"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.support_url == "" and s.privacy_url == ""
    assert s.scheduler_enabled is True
    assert s.max_bot_username == ""


def test_scheduler_can_be_disabled(monkeypatch):
    assert _settings(monkeypatch, SCHEDULER_ENABLED="false").scheduler_enabled is False


def test_reference_paths_relative_to_content_dir(monkeypatch, tmp_path):
    s = _settings(monkeypatch, CONTENT_DIR=str(tmp_path), NDS_FILE="nds-test.yaml")
    assert s.obligations_path == tmp_path / "obligations.yaml"
    assert s.workdays_path == tmp_path / "workdays.yaml"
    assert s.nds_path == Path(tmp_path) / "nds-test.yaml"


@pytest.mark.parametrize(("tz", "utc_hour"), [("Asia/Vladivostok", 0), ("Europe/Kaliningrad", 8)])
def test_zoneinfo_has_russian_timezones(tz, utc_hour):
    """Часовые пояса профиля нужны планировщику; tzdata должна быть в окружении."""
    local = datetime(2030, 6, 15, 10, 0, tzinfo=ZoneInfo(tz))
    assert local.astimezone(UTC).hour == utc_hour
