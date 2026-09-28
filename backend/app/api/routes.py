"""HTTP API для мини-приложения.

Контракт — openapi.yaml в корне (генерируется scripts/export_openapi.py).
"""

import logging
import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ics, items, profile, tasks
from app.api.deps import current_launch, launch_user_id, optional_reference
from app.api.schemas import MeResponse, TaskDraft
from app.api.service import profile_out
from app.calendar.types import Reference
from app.core.db import get_session
from app.core.events import track
from app.core.models import DialogState, Profile

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["miniapp"])
router.include_router(items.router)
router.include_router(tasks.router)
router.include_router(profile.router)
router.include_router(ics.router)
Launch = Annotated[dict, Depends(current_launch)]
Session = Annotated[AsyncSession, Depends(get_session)]
OptionalRef = Annotated[Reference | None, Depends(optional_reference)]

# Событие открытия мини-аппа: в него дописывается start_param из подписанной initData,
# чтобы считать конверсию входа по QR-диплинку (issue #12).
OPENED_EVENT = "miniapp_opened"

# start_param «Изменить» (экран 9) и «Выбрать дату» (экран 10): «task_draft_<id>» (#96).
# Голый «task_draft» — кнопки, отправленные до #96: черновик отдаётся без сверки, как раньше.
DRAFT_PARAM = "task_draft"
_DRAFT_ID_PARAM = re.compile(r"task_draft_([0-9a-z]{1,32})")


def launch_start_param(launch: dict) -> str | None:
    """start_param из подписанной initData: payload диплинка `?start=`.

    Ключ взят из docs/max-api-notes.md (`WebApp.initDataUnsafe.start_param`);
    на живой строке initData не сверен — токена MAX пока нет.
    """
    value = launch.get("start_param")
    return str(value) if value else None


def draft_id_from(start_param: str | None) -> str | None:
    """Id черновика из start_param «task_draft_<id>»; другой start_param — None."""
    match = _DRAFT_ID_PARAM.fullmatch(start_param or "")
    return match.group(1) if match else None


def task_draft(state: DialogState | None, draft_id: str | None = None) -> TaskDraft | None:
    """Черновик из бота: data["task_draft"] = {"title", "due_date": "YYYY-MM-DD", "id"} (T8b).

    Необязательные `remind_hour` и `remind_minute` — время из сообщения (#103): отдаются оба
    или ни одного; черновик без них (времени не было, записан до #103) — оба null.

    `draft_id` — из start_param кнопки (#96): черновик отдаётся, только если id совпал.
    Без него (голый `task_draft`, другой вход) — текущий черновик, как раньше.
    Битый или неполный черновик — null: форма откроется пустой, а не с ошибкой.
    """
    raw = (state.data or {}).get("task_draft") if state is not None else None
    if not isinstance(raw, dict):
        return None
    if draft_id is not None and raw.get("id") != draft_id:
        return None
    title, due = raw.get("title"), raw.get("due_date")
    if not isinstance(title, str) or not isinstance(due, str):
        return None
    try:
        due_date = date.fromisoformat(due)
    except ValueError:
        log.warning("task_draft с неразборчивой датой %r", due)
        return None
    hour, minute = raw.get("remind_hour"), raw.get("remind_minute")
    if not (_is_int_in(hour, 23) and _is_int_in(minute, 59)):
        hour = minute = None  # времени не было (или черновик до #103) — оба null
    return TaskDraft(title=title.strip(), due_date=due_date, remind_hour=hour, remind_minute=minute)


def _is_int_in(value: object, top: int) -> bool:
    """Целое 0…top; bool не считается."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= top


class TrackRequest(BaseModel):
    name: str
    props: dict = {}


@router.get("/me", response_model=MeResponse, summary="Текущий пользователь мини-приложения")
async def me(
    launch: Launch,
    session: Session,
    reference: OptionalRef,
    start_param: Annotated[
        str | None,
        Query(
            description=(
                "Только для dev-режима (ALLOW_DEV_INITDATA): подменяет start_param, "
                "чтобы отлаживать вход по диплинку вне MAX. "
                "С настоящей подписанной initData параметр игнорируется."
            )
        ),
    ] = None,
) -> MeResponse:
    user = launch.get("user") or {}
    effective = launch_start_param(launch)
    if launch.get("is_dev") and start_param:
        effective = start_param
    user_id = launch_user_id(launch)
    profile = draft = None
    draft_id = draft_id_from(effective)
    if user_id is not None:
        profile = profile_out(await session.get(Profile, user_id), reference)
        draft = task_draft(await session.get(DialogState, user_id), draft_id)
    return MeResponse(
        user_id=user_id or 0,
        first_name=user.get("first_name") or user.get("name"),
        is_dev=bool(launch.get("is_dev")),
        start_param=effective,
        has_profile=profile is not None,
        profile=profile,
        draft=draft,
        # Кнопка черновика из старого сообщения: своего черновика нет — форма 17 пустая (#96).
        draft_stale=draft_id is not None and draft is None,
    )


@router.post("/events", status_code=204, summary="Событие аналитики из мини-приложения")
async def post_event(
    body: TrackRequest,
    launch: Launch,
    session: Session,
) -> None:
    user = launch.get("user") or {}
    props = dict(body.props)
    if body.name == OPENED_EVENT:
        signed = launch_start_param(launch)
        if launch.get("is_dev"):
            # Подписи нет вовсе, брать неоткуда — значение от фронта допустимо.
            props.setdefault("start_param", signed)
        else:
            # Источник правды — только подпись, в том числе когда диплинка не было:
            # иначе конверсию из QR накручивают запросом из браузера.
            props["start_param"] = signed
    await track(session, user.get("user_id") or user.get("id"), body.name, props)
    await session.commit()
