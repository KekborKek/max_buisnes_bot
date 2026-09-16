"""Зависимости API мини-приложения: проверка initData из заголовка X-Max-Init-Data."""

import logging

from fastapi import Header, HTTPException

from app.core.config import get_settings
from app.core.initdata import InitDataError, validate_init_data

log = logging.getLogger(__name__)


async def current_launch(x_max_init_data: str | None = Header(default=None)) -> dict:
    s = get_settings()
    if s.app_env == "dev" and s.allow_dev_initdata and x_max_init_data == "dev":
        # локальная разработка вне MAX: подпись не проверяется, пользователь фиктивный
        log.warning(
            "ALLOW_DEV_INITDATA=true: initData не проверяется, "
            "запрос выполнен от фиктивного пользователя. В проде так быть не должно"
        )
        return {"user": {"user_id": 1, "first_name": "Dev"}, "is_dev": True}
    try:
        return validate_init_data(
            x_max_init_data or "", s.max_bot_token, s.initdata_max_age_seconds
        )
    except InitDataError as e:
        raise HTTPException(status_code=401, detail=f"invalid initData: {e}") from e
