"""«Поделиться сроком» (SHARE): приглашение по ссылке `?startapp=share_<code>`.

Отправитель на карточке 16 создаёт приглашение по своему событию, мини-апп отдаёт ссылку
в MAX Bridge `shareMaxContent`. Получатель открывает мини-апп по ссылке и одним нажатием
получает свою задачу с тем же названием и датой. Дата в прошлом допустима (D32): задача
просроченная, без напоминаний. Будущая — как задача из формы 17 по умолчанию: за 1 день в 10:00.

Имя отправителя получателю не отдаём: согласия на передачу персональных данных нет.
"""

import logging
import re
import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_reference, current_time, current_user_id
from app.api.schemas import (
    DEFAULT_REMIND_HOUR,
    DEFAULT_REMIND_MINUTE,
    DEFAULT_REMIND_OFFSET,
    TITLE_MAX_LEN,
    ShareAccepted,
    ShareInput,
    ShareInvite,
    ShareOut,
)
from app.calendar.reminders import sync_task_notification
from app.calendar.types import Reference
from app.core.config import get_settings
from app.core.db import get_session
from app.core.events import track
from app.core.models import SharedItem, SharedItemAccept, Task

log = logging.getLogger(__name__)

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(current_time)]
Ref = Annotated[Reference, Depends(current_reference)]

START_PREFIX = "share_"
# secrets.token_urlsafe(9) → 12 символов [A-Za-z0-9_-]: алфавит startapp, до 512 символов.
CODE_BYTES = 9
CODE_RE = re.compile(r"[A-Za-z0-9_-]{8,32}")
INVITE_NOT_FOUND = {404: {"description": "Приглашения с таким кодом нет"}}


def task_title(title: str) -> str:
    """Название под лимит задачи (60, экран 17): у обязательств бывают длиннее."""
    title = title.strip()
    if len(title) <= TITLE_MAX_LEN:
        return title
    return title[: TITLE_MAX_LEN - 1].rstrip() + "…"


def share_link(code: str) -> str | None:
    """Диплинк мини-аппа (docs/max-api-notes.md, «Диплинки»). Нет имени бота — None."""
    username = get_settings().max_bot_username.strip().lstrip("@")
    if not username:
        return None
    return f"https://max.ru/{username}?startapp={START_PREFIX}{code}"


def share_out(share: SharedItem) -> ShareOut:
    return ShareOut(
        code=share.code, link=share_link(share.code), title=share.title, due_date=share.due_date
    )


async def load_share(session: AsyncSession, code: str) -> SharedItem | None:
    if not CODE_RE.fullmatch(code):
        return None
    return await session.scalar(select(SharedItem).where(SharedItem.code == code))


@router.post("/shares", response_model=ShareOut, summary="Создать приглашение по своему событию")
async def create_share(
    body: ShareInput, user_id: UserId, session: Session, reference: Ref
) -> ShareOut:
    """Повторный вызов по тому же событию с теми же названием и датой — тот же код.

    Мини-апп зовёт его при открытии карточки (ссылка должна быть готова к нажатию: MAX Bridge
    проверяет клик пользователя), поэтому `share_created` пишет мини-апп по нажатию, а не здесь.
    """
    if body.item_type == "obligation":
        uo, ob = await service.own_obligation(session, user_id, body.item_id, reference)
        title, due = ob.title, uo.due_date
    else:
        task = await service.own_task(session, user_id, body.item_id)
        title, due = task.title, task.due_date
    title = task_title(title)

    existing = await session.scalar(
        select(SharedItem)
        .where(
            SharedItem.from_user_id == user_id,
            SharedItem.kind == body.item_type,
            SharedItem.source_item_id == body.item_id,
            SharedItem.title == title,
            SharedItem.due_date == due,
        )
        .order_by(SharedItem.id)
        .limit(1)
    )
    if existing is not None:
        return share_out(existing)

    share = SharedItem(
        code=secrets.token_urlsafe(CODE_BYTES),
        from_user_id=user_id,
        kind=body.item_type,
        source_item_id=body.item_id,
        title=title,
        due_date=due,
    )
    session.add(share)
    await session.commit()
    return share_out(share)


@router.get(
    "/shares/{code}",
    response_model=ShareInvite,
    summary="Приглашение по коду (без данных отправителя)",
    responses=INVITE_NOT_FOUND,
)
async def get_share(code: str, user_id: UserId, session: Session) -> ShareInvite:
    share = await load_share(session, code)
    await track(session, user_id, "share_opened", {"code_valid": share is not None})
    await session.commit()
    if share is None:
        raise HTTPException(status_code=404, detail="share not found")
    return ShareInvite(
        code=share.code, item_type=share.kind, title=share.title, due_date=share.due_date
    )


async def _accepted_task(session: AsyncSession, share_id: int, user_id: int) -> Task | None:
    """Живая задача, созданная этим пользователем по приглашению раньше."""
    accept = await session.scalar(
        select(SharedItemAccept).where(
            SharedItemAccept.share_id == share_id, SharedItemAccept.user_id == user_id
        )
    )
    if accept is None:
        return None
    task = await session.get(Task, accept.task_id)
    return task if task is not None and task.deleted_at is None else None


@router.post(
    "/shares/{code}/accept",
    response_model=ShareAccepted,
    summary="Добавить срок из приглашения себе (идемпотентно)",
    responses=INVITE_NOT_FOUND,
)
async def accept_share(code: str, user_id: UserId, session: Session, now: Now) -> ShareAccepted:
    """Профиль не нужен: получатель может быть новым пользователем (экран 18).

    Повторное принятие тем же пользователем отдаёт ту же задачу (`created: false`). Если ту
    задачу он удалил — создаётся новая.
    """
    share = await load_share(session, code)
    if share is None:
        raise HTTPException(status_code=404, detail="share not found")
    profile = await service.load_profile(session, user_id)
    today = service.user_today(profile, now)

    task = await _accepted_task(session, share.id, user_id)
    if task is not None:
        return ShareAccepted(created=False, task=service.task_card(task, today))

    share_id, kind, title, due = share.id, share.kind, share.title, share.due_date
    await service.ensure_user(session, user_id)
    task = Task(
        user_id=user_id,
        title=title,
        due_date=due,
        remind_offset_days=DEFAULT_REMIND_OFFSET,
        remind_hour=DEFAULT_REMIND_HOUR,
        remind_minute=DEFAULT_REMIND_MINUTE,
    )
    session.add(task)
    await session.flush()
    # Дата в прошлом (D32) — напоминания нет, правило внутри sync_task_notification.
    await sync_task_notification(session, task, tz=service.user_tz(profile), now=now)

    accept = await session.scalar(
        select(SharedItemAccept).where(
            SharedItemAccept.share_id == share_id, SharedItemAccept.user_id == user_id
        )
    )
    if accept is None:
        session.add(SharedItemAccept(share_id=share_id, user_id=user_id, task_id=task.id))
    else:
        accept.task_id = task.id  # прежнюю задачу удалили — привязываем новую

    backdated = due < today
    await track(session, user_id, "share_accepted", {"item_type": kind, "backdated": backdated})
    await track(
        session,
        user_id,
        "task_created",
        {
            "source": "share",
            "remind_offset": DEFAULT_REMIND_OFFSET,
            "backdated": backdated,
            "done_at_create": False,
        },
    )
    try:
        await session.commit()
    except IntegrityError:
        # Два одновременных «Добавить» одного пользователя: второе упёрлось в уникальность.
        await session.rollback()
        existing = await _accepted_task(session, share_id, user_id)
        if existing is None:
            raise
        return ShareAccepted(created=False, task=service.task_card(existing, today))
    return ShareAccepted(created=True, task=service.task_card(task, today))
