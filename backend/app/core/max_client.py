"""Тонкая обёртка над MAX Bot API. Все запросы к MAX — только через этот модуль."""

import asyncio
import logging
import random
import ssl
from typing import Any

import certifi
import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5
_MAX_SLEEP = 4.0  # потолок ожидания; 2 ретрая * 4с = до ~8с в пределах дедлайна вебхука (30с)

# GET безопасно повторить на любой временной ошибке. POST/PUT/DELETE — только на 429 и сетевых
# ошибках: 5xx на POST /messages мог долететь до MAX уже после того, как сообщение отправлено,
# и повтор задвоит его пользователю.
_RETRY_STATUS_IDEMPOTENT = frozenset({429, 500, 502, 503, 504})
_RETRY_STATUS_MUTATING = frozenset({429})
_RETRYABLE_NETWORK_ERRORS = (httpx.ConnectError, httpx.ReadTimeout)


def _build_verify(ca_bundle: str) -> ssl.SSLContext | bool:
    """certifi + корень Минцифры (platform-api2.max.ru им подписан), публичные корни не теряются."""
    if not ca_bundle:
        return True
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=ca_bundle)
    return ctx


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after is not None:
        try:
            return min(float(retry_after), _MAX_SLEEP)
        except ValueError:
            pass
    delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _MAX_SLEEP)
    return random.uniform(0, delay)


class _RateLimiter:
    """Не больше `rps` запросов в секунду: слот освобождается через секунду после захвата."""

    def __init__(self, rps: int = 30) -> None:
        self._semaphore = asyncio.Semaphore(rps)

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        asyncio.get_running_loop().call_later(1.0, self._semaphore.release)


class MaxClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        s = get_settings()
        self._client = httpx.AsyncClient(
            base_url=base_url or s.max_api_base,
            headers={"Authorization": token if token is not None else s.max_bot_token},
            timeout=httpx.Timeout(35.0),
            verify=_build_verify(s.max_ca_bundle),
            transport=transport,
        )
        self._rate_limiter = _RateLimiter()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        retryable_statuses = _RETRY_STATUS_IDEMPOTENT if method == "GET" else _RETRY_STATUS_MUTATING
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            is_last = attempt == _MAX_ATTEMPTS
            await self._rate_limiter.acquire()
            try:
                resp = await self._client.request(method, url, **kwargs)
            except _RETRYABLE_NETWORK_ERRORS:
                if is_last:
                    raise
                log.warning(
                    "MAX API %s %s -> сетевая ошибка, попытка %s/%s",
                    method,
                    url,
                    attempt,
                    _MAX_ATTEMPTS,
                )
                await asyncio.sleep(_retry_delay(attempt, None))
                continue

            if resp.status_code in retryable_statuses and not is_last:
                log.warning(
                    "MAX API %s %s -> %s, попытка %s/%s",
                    method,
                    url,
                    resp.status_code,
                    attempt,
                    _MAX_ATTEMPTS,
                )
                await asyncio.sleep(_retry_delay(attempt, resp.headers.get("Retry-After")))
                continue

            if resp.status_code >= 400:
                log.error("MAX API %s %s -> %s %s", method, url, resp.status_code, resp.text[:500])
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def send_message(
        self,
        text: str,
        *,
        user_id: int | None = None,
        chat_id: int | None = None,
        attachments: list[dict] | None = None,
        fmt: str | None = "markdown",
    ) -> dict:
        params = {"user_id": user_id} if user_id is not None else {"chat_id": chat_id}
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("POST", "/messages", params=params, json=body)

    async def edit_message(
        self,
        message_id: str,
        text: str,
        *,
        attachments: list[dict] | None = None,
        fmt: str | None = "markdown",
    ) -> dict:
        """PUT /messages?message_id=… — https://dev.max.ru/docs-api/methods/PUT/messages

        Тело — NewMessageBody. `attachments=None` — поле не передаётся, вложения (кнопки)
        остаются как были; `attachments=[]` — все вложения удаляются («сообщение без кнопок»);
        список — клавиатура заменяется целиком (так убирают одну кнопку).
        Лимит MAX: не больше двух правок в секунду в одном диалоге.
        """
        body: dict[str, Any] = {"text": text}
        if attachments is not None:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("PUT", "/messages", params={"message_id": message_id}, json=body)

    async def send_typing(self, chat_id: int) -> dict:
        """Индикатор «печатает»: POST /chats/{chatId}/actions, action = typing_on.

        https://dev.max.ru/docs-api/methods/POST/chats/-chatId-/actions — в документации
        метод описан для групповых чатов, про диалог с ботом не сказано [сверить].
        Вызывающий код не должен падать, если индикатор не сработал.
        """
        return await self._request(
            "POST", f"/chats/{chat_id}/actions", json={"action": "typing_on"}
        )

    async def answer_callback(self, callback_id: str, notification: str | None = None) -> dict:
        body = {"notification": notification} if notification else {}
        return await self._request(
            "POST", "/answers", params={"callback_id": callback_id}, json=body
        )

    async def get_updates(self, marker: int | None = None, timeout: int = 30) -> dict:
        params: dict[str, Any] = {"timeout": timeout}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def subscribe_webhook(self, url: str, secret: str | None = None) -> dict:
        body: dict[str, Any] = {"url": url}
        if secret:
            body["secret"] = secret
        return await self._request("POST", "/subscriptions", json=body)

    async def unsubscribe_webhook(self, url: str) -> dict:
        return await self._request("DELETE", "/subscriptions", params={"url": url})
