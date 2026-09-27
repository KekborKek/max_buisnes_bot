import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/test.db")
os.environ.setdefault("CONTENT_DIR", str(ROOT / "content"))
os.environ.setdefault("SEED_DIR", str(ROOT / "seed"))
os.environ.setdefault("MAX_BOT_TOKEN", "test-token")
os.environ.setdefault("MAX_WEBHOOK_SECRET", "test-secret")

FIXTURES = Path(__file__).parent / "fixtures" / "updates"


def load_update(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeMax:
    """Подменяет MaxClient: запоминает отправленное вместо реальных запросов."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.answered: list[str] = []
        self.edited: list[dict] = []
        self.typing: list[int] = []

    async def send_message(self, text, *, user_id=None, chat_id=None, attachments=None, fmt=None):
        self.sent.append(
            {"text": text, "user_id": user_id, "chat_id": chat_id, "attachments": attachments}
        )
        return {}

    async def answer_callback(self, callback_id, notification=None):
        self.answered.append(callback_id)
        return {}

    async def edit_message(self, message_id, text, *, attachments=None, fmt=None):
        self.edited.append({"message_id": message_id, "text": text, "attachments": attachments})
        return {}

    async def send_typing(self, chat_id):
        self.typing.append(chat_id)
        return {}

    async def close(self):
        pass


@pytest.fixture
def fake_max() -> FakeMax:
    return FakeMax()


@pytest.fixture
def fixture_reference(monkeypatch):
    """Справочники из backend/tests/fixtures вместо файлов аналитика (их в content/ может не быть).

    Подменяет `loader.get_reference` — бот и сборка зовут его через модуль. Кеш настоящей
    функции сбрасываем, чтобы она не отдала результат, закешированный другим тестом.
    """
    from app.calendar import loader
    from app.calendar.types import Reference

    folder = Path(__file__).parent / "fixtures"
    reference = Reference(
        catalog=loader.load_catalog(folder / "obligations.yaml"),
        workdays=loader.load_workdays(folder / "workdays.yaml"),
        nds=loader.load_nds(folder / "nds.yaml"),
    )
    loader.get_reference.cache_clear()
    monkeypatch.setattr(loader, "get_reference", lambda: reference)
    return reference


@pytest.fixture(autouse=True)
def fixture_regime_limits(monkeypatch):
    """Лимиты режимов (#107) из backend/tests/fixtures, а не content/regime_limits.yaml.

    Экран 3 зовёт `loader.get_regime_limits` в каждом онбординге, поэтому подмена — для всех
    тестов. Реальный файл проверяет только test_regime_limits напрямую через load_regime_limits.
    """
    from app.calendar import loader

    config = loader.load_regime_limits(Path(__file__).parent / "fixtures" / "regime_limits.yaml")
    loader.get_regime_limits.cache_clear()
    monkeypatch.setattr(loader, "get_regime_limits", lambda: config)
    return config


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.core.db import Base, engine, init_db

    await init_db()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# --- API мини-приложения (T10) ------------------------------------------------------------


class MiniappApi:
    """Клиент API мини-аппа с подписанной initData, подменёнными часами и справочником."""

    def __init__(self, client, token: str, now) -> None:
        self.client = client
        self.token = token
        self.now = now  # «сейчас» для API; тест может переставить

    def headers(self, user_id: int, start_param: str | None = None) -> dict[str, str]:
        import hashlib
        import hmac
        import time
        from urllib.parse import urlencode

        params = {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"user_id": user_id, "first_name": "Тест"}),
        }
        if start_param is not None:  # payload кнопки open_app / диплинка — в подписи (#96)
            params["start_param"] = start_param
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
        params["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        return {"X-Max-Init-Data": urlencode(params)}

    async def request(
        self,
        method: str,
        path: str,
        user_id: int | None,
        *,
        start_param: str | None = None,
        **kwargs,
    ):
        headers = self.headers(user_id, start_param) if user_id is not None else {}
        return await self.client.request(method, path, headers=headers, **kwargs)


@pytest.fixture
def test_reference():
    """ТЕСТОВЫЕ ДАННЫЕ: справочники из backend/tests/fixtures/, не файлы аналитика."""
    from app.calendar import loader
    from app.calendar.types import Reference

    base = Path(__file__).parent / "fixtures"
    return Reference(
        catalog=loader.load_catalog(base / "obligations.yaml"),
        workdays=loader.load_workdays(base / "workdays.yaml"),
        nds=loader.load_nds(base / "nds.yaml"),
    )


@pytest.fixture
async def miniapp_api(monkeypatch, test_reference):
    """API мини-аппа: dev-режим выключен, «сейчас» — 23.09.2026 10:00 по Москве."""
    from datetime import UTC, datetime

    import httpx

    from app.api import deps
    from app.core.config import Settings
    from app.main import app

    token = "test-token"
    s = Settings(_env_file=None, app_env="dev", allow_dev_initdata=False, max_bot_token=token)
    monkeypatch.setattr(deps, "get_settings", lambda: s)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        api = MiniappApi(client, token, datetime(2026, 9, 23, 7, tzinfo=UTC))
        app.dependency_overrides[deps.current_time] = lambda: api.now
        app.dependency_overrides[deps.current_reference] = lambda: test_reference
        app.dependency_overrides[deps.optional_reference] = lambda: test_reference
        try:
            yield api
        finally:
            app.dependency_overrides.pop(deps.current_time, None)
            app.dependency_overrides.pop(deps.current_reference, None)
            app.dependency_overrides.pop(deps.optional_reference, None)
