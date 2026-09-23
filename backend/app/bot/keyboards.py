"""Конструкторы inline-клавиатур MAX.

Лимиты: до 210 кнопок, 30 рядов, 7 кнопок в ряду (3 — для link/open_app/request_contact/
request_geo_location). В payload кладём короткий id, а не данные.
Формат вложения сверять с docs: dev.max.ru/docs-api (объект InlineKeyboardAttachmentRequest).
"""

from app.core.config import get_settings


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def link(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def request_contact(text: str) -> dict:
    return {"type": "request_contact", "text": text}


def open_app(
    text: str,
    payload: str | None = None,
    *,
    web_app: str | None = None,
    contact_id: int | None = None,
) -> dict:
    """Кнопка запуска мини-приложения (OpenAppButton, D27).

    Поля по схеме https://dev.max.ru/docs-api/methods/POST/messages (OpenAppButton):
    `web_app` — публичное имя бота или ссылка на него, `contact_id` — id бота,
    `payload` — параметр запуска, приходит в мини-апп как `start_param` в initData
    (например `task_draft`, `item_obligation_42`).

    Без `web_app` и `contact_id` подставляется MAX_BOT_USERNAME. Если он пуст, кнопка
    собирается без адреса и не падает, но вызывающий код такую кнопку не показывает:
    проверяйте `get_settings().max_bot_username` (как SUPPORT_URL в D13).
    """
    button: dict = {"type": "open_app", "text": text}
    if web_app is None and contact_id is None:
        web_app = get_settings().max_bot_username or None
    if web_app is not None:
        button["web_app"] = web_app
    if contact_id is not None:
        button["contact_id"] = contact_id
    if payload is not None:
        button["payload"] = payload
    return button


def inline_keyboard(*rows: list[dict]) -> dict:
    return {"type": "inline_keyboard", "payload": {"buttons": [list(r) for r in rows]}}
