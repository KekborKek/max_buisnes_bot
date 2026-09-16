"""Запись событий аналитики.

Использование: await track(session, user_id, "checklist_done", {"point": 7})
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Event

log = logging.getLogger(__name__)


async def track(
    session: AsyncSession, user_id: int | None, name: str, props: dict | None = None
) -> None:
    session.add(Event(user_id=user_id, name=name, props=props or {}))
    log.info("event %s user=%s props=%s", name, user_id, props)
