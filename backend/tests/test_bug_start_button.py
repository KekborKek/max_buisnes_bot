"""БАГ №1: «После нажатия кнопки "Начать" ничего не происходит».

Причина: в `backend/requirements.txt` не объявлен `greenlet`. SQLAlchemy 2 ставит его
автоматически только на x86_64/aarch64/amd64/win32 (см. Requires-Dist в метаданных пакета),
а на macOS arm64 `platform_machine == "arm64"` — и пакет не ставится. Без greenlet любой
await к БД падает с ValueError, апдейт `bot_started` не доходит до обработчика, и бот молчит.

Тесты ничего не исправляют — они фиксируют баг и должны падать до починки.
"""

import re
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.dispatcher import process_update
from tests.conftest import load_update

REQUIREMENTS = Path(__file__).resolve().parents[2] / "backend" / "requirements.txt"

# Дословный текст ошибки SQLAlchemy 2.0.54 при отсутствии greenlet.
GREENLET_ERROR = "the greenlet library is required. No module named 'greenlet'"


def _requirement_lines() -> list[str]:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    return [line for line in (ln.split("#", 1)[0].strip() for ln in text.splitlines()) if line]


def test_requirements_declare_greenlet_for_async_sqlalchemy():
    """Зависимость greenlet должна быть объявлена явно, иначе на macOS arm64
    её не будет и ни один запрос к БД не выполнится."""
    lines = _requirement_lines()
    has_greenlet = any(re.match(r"greenlet\b", line, re.IGNORECASE) for line in lines)
    has_asyncio_extra = any(
        re.match(r"sqlalchemy\s*\[[^\]]*asyncio", line, re.IGNORECASE) for line in lines
    )
    assert has_greenlet or has_asyncio_extra, (
        "backend/requirements.txt не объявляет greenlet и не использует SQLAlchemy[asyncio]: "
        "на macOS arm64 greenlet не устанавливается, любой await к БД падает, "
        "и бот молча игнорирует нажатие «Начать»"
    )


async def test_start_button_does_not_fail_silently(fake_max):
    """Если обращение к БД падает, пользователь всё равно должен получить ответ.

    Сейчас исключение из `await session.flush()` (dispatcher.py:51) летит наружу из
    `process_update`, минуя общий `except Exception` (dispatcher.py:62): фоновая задача
    вебхука умирает молча, MAX уже получил 200, пользователь не видит ничего.
    """

    async def broken_flush(self, *args, **kwargs):
        raise ValueError(GREENLET_ERROR)

    with patch.object(AsyncSession, "flush", broken_flush):
        await process_update(load_update("bot_started"), fake_max)

    assert fake_max.sent, "бот не отправил пользователю ни одного сообщения на «Начать»"
