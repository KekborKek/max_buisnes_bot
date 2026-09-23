"""MaxClient: ретраи 429/5xx и лимит 30 rps (#10); edit_message, send_typing, open_app (#35)."""

import asyncio
import json

import httpx
import pytest

from app.bot import keyboards as kb
from app.core import max_client as mc
from app.core.config import Settings
from app.core.max_client import MaxClient, _RateLimiter


async def _instant_sleep(_seconds: float) -> None:
    return None


def _client(handler) -> MaxClient:
    transport = httpx.MockTransport(handler)
    return MaxClient(token="t", base_url="https://example.invalid", transport=transport)


async def test_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(mc.asyncio, "sleep", _instant_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"code": "rate_limit"})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        assert await client.get_me() == {"ok": True}
        assert calls["n"] == 2
    finally:
        await client.close()


async def test_respects_retry_after_header(monkeypatch):
    delays = []

    async def recording_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(mc.asyncio, "sleep", recording_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        await client.get_me()
    finally:
        await client.close()
    assert delays == [2.0]


async def test_post_not_retried_on_500(monkeypatch):
    monkeypatch.setattr(mc.asyncio, "sleep", _instant_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, json={})

    client = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.send_message("hi", chat_id=1)
        assert calls["n"] == 1
    finally:
        await client.close()


async def test_get_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setattr(mc.asyncio, "sleep", _instant_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={})

    client = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_me()
        assert calls["n"] == 3
    finally:
        await client.close()


async def test_retries_network_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(mc.asyncio, "sleep", _instant_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        assert await client.get_me() == {"ok": True}
    finally:
        await client.close()


async def test_post_retries_on_network_error_not_on_5xx_status(monkeypatch):
    """Пункт 3 issue #10: сетевые ошибки ретраятся и для POST, а вот 5xx-статус — нет."""
    monkeypatch.setattr(mc.asyncio, "sleep", _instant_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        assert await client.send_message("hi", chat_id=1) == {"ok": True}
        assert calls["n"] == 2
    finally:
        await client.close()


async def test_rate_limiter_blocks_after_limit():
    limiter = _RateLimiter(rps=2)
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(), timeout=0.05)


# --- T0 (#35): редактирование сообщения, «печатает», кнопка open_app ---------------------
# Структура — https://dev.max.ru/docs-api/methods/PUT/messages (NewMessageBody),
# https://dev.max.ru/docs-api/methods/POST/chats/-chatId-/actions (ActionRequestBody),
# OpenAppButton в схеме https://dev.max.ru/docs-api/methods/POST/messages


def _recording_client(seen: list[httpx.Request]) -> MaxClient:
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True})

    return _client(handler)


async def test_edit_message_request_matches_docs():
    seen: list[httpx.Request] = []
    client = _recording_client(seen)
    try:
        result = await client.edit_message("mid.abc123", "Новый текст")
    finally:
        await client.close()

    assert result == {"success": True}
    (req,) = seen
    assert req.method == "PUT"
    assert req.url.path == "/messages"
    assert dict(req.url.params) == {"message_id": "mid.abc123"}
    # attachments не передан → кнопки в сообщении не меняются
    assert json.loads(req.content) == {"text": "Новый текст", "format": "markdown"}


async def test_edit_message_empty_attachments_removes_buttons():
    seen: list[httpx.Request] = []
    client = _recording_client(seen)
    try:
        await client.edit_message("mid.1", "Без кнопок", attachments=[], fmt=None)
    finally:
        await client.close()

    assert json.loads(seen[0].content) == {"text": "Без кнопок", "attachments": []}


async def test_edit_message_replaces_keyboard():
    keyboard = kb.inline_keyboard([kb.callback("Отметить", "r:done:obligation:1")])
    seen: list[httpx.Request] = []
    client = _recording_client(seen)
    try:
        await client.edit_message("mid.1", "Текст", attachments=[keyboard])
    finally:
        await client.close()

    assert json.loads(seen[0].content)["attachments"] == [keyboard]


async def test_edit_message_not_retried_on_500(monkeypatch):
    monkeypatch.setattr(mc.asyncio, "sleep", _instant_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, json={})

    client = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.edit_message("mid.1", "x")
    finally:
        await client.close()
    assert calls["n"] == 1


async def test_send_typing_request_matches_docs():
    seen: list[httpx.Request] = []
    client = _recording_client(seen)
    try:
        await client.send_typing(-100500)
    finally:
        await client.close()

    (req,) = seen
    assert req.method == "POST"
    assert req.url.path == "/chats/-100500/actions"
    assert json.loads(req.content) == {"action": "typing_on"}


def test_open_app_button_uses_bot_username(monkeypatch):
    monkeypatch.setattr(
        kb, "get_settings", lambda: Settings(_env_file=None, max_bot_username="pareto_bot")
    )

    assert kb.open_app("Открыть календарь") == {
        "type": "open_app",
        "text": "Открыть календарь",
        "web_app": "pareto_bot",
    }
    assert kb.open_app("Изменить", "task_draft")["payload"] == "task_draft"


def test_open_app_button_explicit_target_wins(monkeypatch):
    monkeypatch.setattr(
        kb, "get_settings", lambda: Settings(_env_file=None, max_bot_username="pareto_bot")
    )

    assert kb.open_app("Открыть", "item_obligation_42", contact_id=987) == {
        "type": "open_app",
        "text": "Открыть",
        "contact_id": 987,
        "payload": "item_obligation_42",
    }


def test_open_app_without_username_does_not_fail(monkeypatch):
    monkeypatch.setattr(kb, "get_settings", lambda: Settings(_env_file=None, max_bot_username=""))

    assert kb.open_app("Открыть календарь") == {"type": "open_app", "text": "Открыть календарь"}
