"""Long polling — ТОЛЬКО для разработки (python -m app.polling).
Нельзя одновременно с вебхуком: сначала удалите подписку (scripts/webhook.py delete)."""

import asyncio
import logging

from app.bot.dispatcher import process_update
from app.core.db import init_db
from app.core.max_client import MaxClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("polling")


async def main() -> None:
    await init_db()
    client = MaxClient()
    me = await client.get_me()
    log.info("polling as bot: %s", me)
    marker: int | None = None
    try:
        while True:
            try:
                data = await client.get_updates(marker=marker, timeout=30)
            except Exception:
                log.exception("get_updates failed, retry in 3s")
                await asyncio.sleep(3)
                continue
            for update in data.get("updates", []):
                await process_update(update, client)
            marker = data.get("marker", marker)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
