"""Зависимости API мини-приложения: проверка initData из заголовка X-Max-Init-Data."""

import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.calendar.loader import get_reference
from app.calendar.types import Reference, ReferenceFileError
from app.core.config import get_settings
from app.core.initdata import InitDataError, validate_init_data

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _warn_dev_initdata_once() -> None:
    """Один раз на процесс: мини-апп дёргает API часто, warning на каждый запрос — шум."""
    log.warning(
        "ALLOW_DEV_INITDATA=true: initData не проверяется, "
        "запросы с заголовком X-Max-Init-Data: dev выполняются от фиктивного "
        "пользователя. В проде так быть не должно"
    )


INIT_DATA_HEADER_DESCRIPTION = (
    "Подписанные данные запуска мини-приложения из MAX Bridge (`window.WebApp.initData`). "
    "Бэкенд проверяет подпись токеном бота и берёт из них пользователя. "
    "Без заголовка или с неверной подписью — 401."
)


async def current_launch(
    x_max_init_data: str | None = Header(default=None, description=INIT_DATA_HEADER_DESCRIPTION),
) -> dict:
    s = get_settings()
    # Флаг не зависит от наличия токена: иначе dev-режим ломался бы в час, когда
    # MAX_BOT_TOKEN попадёт в .env (issue #8). APP_ENV=prod запрещает его всегда.
    if s.app_env == "dev" and s.allow_dev_initdata and x_max_init_data == "dev":
        _warn_dev_initdata_once()
        return {"user": {"user_id": 1, "first_name": "Dev"}, "is_dev": True}
    try:
        return validate_init_data(
            x_max_init_data or "", s.max_bot_token, s.initdata_max_age_seconds
        )
    except InitDataError as e:
        raise HTTPException(status_code=401, detail=f"invalid initData: {e}") from e


def launch_user_id(launch: dict) -> int | None:
    """id пользователя MAX из подписанной initData (поле `user_id`, запасной вариант `id`)."""
    user = launch.get("user") or {}
    raw = user.get("user_id") or user.get("id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


async def current_user_id(launch: Annotated[dict, Depends(current_launch)]) -> int:
    """Владелец запроса. Без пользователя в initData данных календаря не отдаём."""
    user_id = launch_user_id(launch)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid initData: no user")
    return user_id


def current_time() -> datetime:
    """«Сейчас» в UTC. Отдельная зависимость — тесты подменяют часы."""
    return datetime.now(UTC)


def current_reference() -> Reference:
    """Справочник обязательств процесса. Тесты подменяют фикстурой из backend/tests/fixtures/."""
    return get_reference()


def optional_reference() -> Reference | None:
    """Справочник или None, если файл не загрузился (ReferenceFileError) — вместо голого 500.

    /api/me — вход в мини-апп, из-за справочника он падать не должен (экран 18): дата сверки
    просто null. «Пересобрать» (экран 19) на None отвечает 503 и пишет событие error.
    """
    try:
        return get_reference()
    except ReferenceFileError as e:
        log.warning("справочник недоступен: %s", e)
        return None
