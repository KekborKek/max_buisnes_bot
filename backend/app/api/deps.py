"""Зависимости API мини-приложения: проверка initData из заголовка X-Max-Init-Data."""

from fastapi import Header, HTTPException

from app.core.config import get_settings
from app.core.initdata import InitDataError, validate_init_data


async def current_launch(x_max_init_data: str | None = Header(default=None)) -> dict:
    s = get_settings()
    if s.app_env == "dev" and not s.max_bot_token and x_max_init_data == "dev":
        # локальная разработка без токена: фиктивный пользователь
        return {"user": {"user_id": 1, "first_name": "Dev"}, "is_dev": True}
    try:
        return validate_init_data(
            x_max_init_data or "", s.max_bot_token, s.initdata_max_age_seconds
        )
    except InitDataError as e:
        raise HTTPException(status_code=401, detail=f"invalid initData: {e}") from e
