"""Тонкая обёртка над MAX Bot API. Все запросы к MAX — только через этот модуль."""

import logging
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


class MaxClient:
    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        s = get_settings()
        self._client = httpx.AsyncClient(
            base_url=base_url or s.max_api_base,
            headers={"Authorization": token if token is not None else s.max_bot_token},
            timeout=httpx.Timeout(35.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        resp = await self._client.request(method, url, **kwargs)
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
