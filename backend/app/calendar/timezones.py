"""Часовые пояса, из которых выбирает пользователь: вопрос 4 экрана 2 и экран 13.

Один список на бота (onboarding) и API мини-аппа (`PUT /api/profile/settings`), чтобы
API не импортировал обработчики бота. Храним IANA-идентификатор, не сдвиг (product.md).
"""

from app.core.models import DEFAULT_TIMEZONE

# D3: порядок кнопок списка «Другой пояс», по два в ряд. Подписи — onboarding.tz.<IANA>.
OTHER_TIMEZONES = (
    "Europe/Kaliningrad",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Yakutsk",
    "Asia/Vladivostok",
    "Asia/Magadan",
    "Asia/Kamchatka",
)
TIMEZONES = (DEFAULT_TIMEZONE, *OTHER_TIMEZONES)
