"""Импорт модулей регистрирует обработчики в роутере."""

from app.bot.handlers import (  # noqa: F401
    about,
    calendar_ready,
    demo,
    digest,
    done,
    fallback,
    howto,
    nds_answer,
    onboarding,
    reminders,
    service,
    start,
    task_chat,
)
