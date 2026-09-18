"""HTTP API для мини-приложения.

Контракт — openapi.yaml в корне (генерируется scripts/export_openapi.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_launch
from app.core.db import get_session
from app.core.events import track

router = APIRouter(prefix="/api", tags=["miniapp"])
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


class MeResponse(BaseModel):
    user_id: int
    first_name: str | None = None
    is_dev: bool = False
    start_param: str | None = None


class TrackRequest(BaseModel):
    name: str
    props: dict = {}


@router.get("/me", response_model=MeResponse, summary="Текущий пользователь мини-приложения")
async def me(
    launch: Launch,
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
    return MeResponse(
        user_id=int(user.get("user_id") or user.get("id") or 0),
        first_name=user.get("first_name") or user.get("name"),
        is_dev=bool(launch.get("is_dev")),
        start_param=effective,
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
