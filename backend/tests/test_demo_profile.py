"""scripts/demo_profile.py: ТЕСТОВЫЕ ДАННЫЕ для мини-аппа в браузере без MAX."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.models import Profile, User, UserObligation

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_profile.py"
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def load_script():
    spec = importlib.util.spec_from_file_location("demo_profile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_creates_profile_and_calendar_for_dev_user(fixture_reference):
    script = load_script()
    total = await script.create_demo_profile(NOW)

    async with SessionLocal() as session:
        user = await session.get(User, script.DEV_USER_ID)
        profile = await session.get(Profile, script.DEV_USER_ID)
        count = await session.scalar(
            select(func.count())
            .select_from(UserObligation)
            .where(UserObligation.user_id == script.DEV_USER_ID)
        )
    assert "ТЕСТОВЫЕ ДАННЫЕ" in user.name
    assert profile.regime == "usn6"
    assert profile.calendar_built_at is not None
    assert total > 0
    assert count == total


async def test_second_run_does_not_duplicate(fixture_reference):
    script = load_script()
    first = await script.create_demo_profile(NOW)
    second = await script.create_demo_profile(NOW)
    assert first == second
