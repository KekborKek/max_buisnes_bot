"""Управление подпиской вебхука.
python scripts/webhook.py set     — подписать PUBLIC_BASE_URL/webhook/max
python scripts/webhook.py delete  — отписать
python scripts/webhook.py me      — проверить токен (GET /me)
python scripts/webhook.py list    — показать активные подписки (GET /subscriptions)
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.max_client import MaxClient  # noqa: E402


def _mask_subscriptions(payload: dict) -> dict:
    """Секрет подписки — только факт наличия ("***"), не значение (issue #21).

    Если в ответе MAX нет поля `subscriptions` или у подписки нет поля `secret` — ничего
    не выдумываем, печатаем как пришло.
    """
    subscriptions = payload.get("subscriptions")
    if not isinstance(subscriptions, list):
        return payload

    def mask(item: Any) -> Any:
        if not isinstance(item, dict) or "secret" not in item:
            return item
        return {**item, "secret": "***"}

    return {**payload, "subscriptions": [mask(item) for item in subscriptions]}


async def main(cmd: str) -> None:
    s = get_settings()
    client = MaxClient()
    url = f"{s.public_base_url.rstrip('/')}/webhook/max"
    try:
        if cmd == "set":
            print(await client.subscribe_webhook(url, s.max_webhook_secret or None))
        elif cmd == "delete":
            print(await client.unsubscribe_webhook(url))
        elif cmd == "me":
            print(await client.get_me())
        elif cmd == "list":
            print(_mask_subscriptions(await client.get_subscriptions()))
        else:
            print(__doc__)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
