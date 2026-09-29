"""ТЕСТОВЫЕ ДАННЫЕ: профиль и календарь для пользователя «Dev» — мини-апп в браузере без MAX.

Вне MAX мини-апп ходит в API с заголовком `X-Max-Init-Data: dev` (при ALLOW_DEV_INITDATA=true),
и бэкенд отвечает от пользователя user_id=1. Профиль создаётся онбордингом в боте, а бот без
токена MAX локально не работает — этот скрипт отвечает на четыре вопроса онбординга за него
и собирает календарь, как кнопка «Собрать календарь».

    docker compose exec backend python scripts/demo_profile.py

Повторный запуск безопасен: сборка идемпотентна. При APP_ENV=prod скрипт ничего не делает.
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendar import dates, loader  # noqa: E402
from app.calendar.build import build_calendar  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.models import Profile, User  # noqa: E402

DEV_USER_ID = 1  # пользователь, от которого API отвечает на `X-Max-Init-Data: dev` (api/deps.py)
DEV_NAME = "Dev (ТЕСТОВЫЕ ДАННЫЕ)"
ANSWERS = {
    "income_band": "10_20",
    "regime": "usn6",
    "has_employees": False,
    "timezone": "Europe/Moscow",
}


async def create_demo_profile(now: datetime | None = None) -> int:
    """Создаёт или обновляет профиль Dev и собирает календарь. Возвращает число сроков."""
    now = now or datetime.now(UTC)
    reference = loader.get_reference()
    async with SessionLocal() as session:
        if await session.get(User, DEV_USER_ID) is None:
            session.add(User(user_id=DEV_USER_ID, name=DEV_NAME))
        profile = await session.get(Profile, DEV_USER_ID)
        if profile is None:
            profile = Profile(user_id=DEV_USER_ID)
            session.add(profile)
        for field, value in ANSWERS.items():
            setattr(profile, field, value)
        income_year = now.astimezone(ZoneInfo(ANSWERS["timezone"])).year - 1
        profile.nds_payer = dates.nds_payer(ANSWERS["income_band"], income_year, reference.nds)
        await session.flush()
        result = await build_calendar(session, DEV_USER_ID, now=now, reference=reference)
        await session.commit()
    return result.total


async def main() -> int:
    if get_settings().app_env == "prod":
        print("APP_ENV=prod: тестовый профиль на проде не создаётся.")
        return 1
    await init_db()
    total = await create_demo_profile()
    print(f"ТЕСТОВЫЕ ДАННЫЕ: профиль Dev (user_id={DEV_USER_ID}) готов, сроков: {total}.")
    print("Откройте мини-апп в браузере: http://localhost:8080")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
