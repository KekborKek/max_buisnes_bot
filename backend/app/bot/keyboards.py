"""Конструкторы inline-клавиатур MAX.

Лимиты: до 210 кнопок, 30 рядов, 7 кнопок в ряду (3 — для link/open_app/request_contact/
request_geo_location). В payload кладём короткий id, а не данные.
Формат вложения сверять с docs: dev.max.ru/docs-api (объект InlineKeyboardAttachmentRequest).
"""


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def link(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def request_contact(text: str) -> dict:
    return {"type": "request_contact", "text": text}


def inline_keyboard(*rows: list[dict]) -> dict:
    return {"type": "inline_keyboard", "payload": {"buttons": [list(r) for r in rows]}}
