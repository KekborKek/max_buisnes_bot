"""Зависимости API мини-приложения: проверка initData из заголовка X-Max-Init-Data."""

import logging
from functools import lru_cache

from fastapi import Header, HTTPException

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


async def current_launch(x_max_init_data: str | None = Header(default=None)) -> dict:
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
