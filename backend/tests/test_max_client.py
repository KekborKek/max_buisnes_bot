"""Issue #10: ретраи 429/5xx с бэкоффом и лимит 30 rps в MaxClient."""

import asyncio

import httpx
import pytest

from app.core import max_client as mc
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
