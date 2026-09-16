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

    async def send_message(self, text, *, user_id=None, chat_id=None, attachments=None, fmt=None):
        self.sent.append(
            {"text": text, "user_id": user_id, "chat_id": chat_id, "attachments": attachments}
        )
        return {}

    async def answer_callback(self, callback_id, notification=None):
        self.answered.append(callback_id)
        return {}

    async def close(self):
        pass


@pytest.fixture
def fake_max() -> FakeMax:
    return FakeMax()


@pytest.fixture(autouse=True)
async def fresh_db():
    from app.core.db import Base, engine, init_db

    await init_db()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
