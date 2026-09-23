"""Импорт модулей регистрирует обработчики в роутере."""

from app.bot.handlers import (  # noqa: F401
    about,
    calendar_ready,
    fallback,
    nds_answer,
    onboarding,
    reminders,
    service,
    start,
)
