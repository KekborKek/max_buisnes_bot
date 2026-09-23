"""Импорт модулей регистрирует обработчики в роутере."""

from app.bot.handlers import (  # noqa: F401
    calendar_ready,
    done,
    fallback,
    howto,
    nds_answer,
    onboarding,
    reminders,
    service,
    start,
)
