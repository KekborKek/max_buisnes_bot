"""HTTP API для мини-приложения.

Контракт — openapi.yaml в корне (генерируется scripts/export_openapi.py).
"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import items, tasks
from app.api.deps import current_launch, launch_user_id
from app.api.schemas import MeResponse, ProfileOut, TaskDraft
from app.calendar.reminders import as_utc
from app.core.db import get_session
from app.core.events import track
from app.core.models import DialogState, Profile

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["miniapp"])
router.include_router(items.router)
router.include_router(tasks.router)
Launch = Annotated[dict, Depends(current_launch)]
Session = Annotated[AsyncSession, Depends(get_session)]

# Событие открытия мини-аппа: в него дописывается start_param из подписанной initData,
# чтобы считать конверсию входа по QR-диплинку (issue #12).
OPENED_EVENT = "miniapp_opened"


def launch_start_param(launch: dict) -> str | None:
    """start_param из подписанной initData: payload диплинка `?start=`.

    Ключ взят из docs/max-api-notes.md (`WebApp.initDataUnsafe.start_param`);
    на живой строке initData не сверен — токена MAX пока нет.
    """
    value = launch.get("start_param")
    return str(value) if value else None


def profile_out(profile: Profile | None) -> ProfileOut | None:
    """Профиль считается заполненным, когда есть все ответы онбординга (D24)."""
    if (
        profile is None
        or profile.income_band is None
        or profile.regime is None
        or profile.has_employees is None
    ):
        return None
    return ProfileOut(
        income_band=profile.income_band,
        regime=profile.regime,
        has_employees=profile.has_employees,
        timezone=profile.timezone,
        nds_payer=profile.nds_payer,
        calendar_built_at=(
            as_utc(profile.calendar_built_at) if profile.calendar_built_at else None
        ),
    )


def task_draft(state: DialogState | None) -> TaskDraft | None:
    """Черновик из бота: data["task_draft"] = {"title": str, "due_date": "YYYY-MM-DD"} (T8b).

    Битый или неполный черновик — null: форма откроется пустой, а не с ошибкой.
    """
    raw = (state.data or {}).get("task_draft") if state is not None else None
    if not isinstance(raw, dict):
        return None
    title, due = raw.get("title"), raw.get("due_date")
    if not isinstance(title, str) or not isinstance(due, str):
        return None
    try:
        due_date = date.fromisoformat(due)
    except ValueError:
        log.warning("task_draft с неразборчивой датой %r", due)
        return None
    return TaskDraft(title=title.strip(), due_date=due_date)


class TrackRequest(BaseModel):
    name: str
    props: dict = {}


@router.get("/me", response_model=MeResponse, summary="Текущий пользователь мини-приложения")
async def me(
    launch: Launch,
    session: Session,
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
    if user_id is not None:
        profile = profile_out(await session.get(Profile, user_id))
        draft = task_draft(await session.get(DialogState, user_id))
    return MeResponse(
        user_id=user_id or 0,
        first_name=user.get("first_name") or user.get("name"),
        is_dev=bool(launch.get("is_dev")),
        start_param=effective,
        has_profile=profile is not None,
        profile=profile,
        draft=draft,
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
