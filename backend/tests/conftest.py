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
async def fresh_db():
    from app.core.db import Base, engine, init_db

    await init_db()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
