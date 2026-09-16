"""HTTP API для мини-приложения.

Контракт — openapi.yaml в корне (генерируется scripts/export_openapi.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_launch
from app.core.db import get_session
from app.core.events import track

router = APIRouter(prefix="/api", tags=["miniapp"])
Launch = Annotated[dict, Depends(current_launch)]
Session = Annotated[AsyncSession, Depends(get_session)]


class MeResponse(BaseModel):
    user_id: int
    first_name: str | None = None
    is_dev: bool = False


class TrackRequest(BaseModel):
    name: str
    props: dict = {}


@router.get("/me", response_model=MeResponse, summary="Текущий пользователь мини-приложения")
async def me(launch: Launch) -> MeResponse:
    user = launch.get("user") or {}
    return MeResponse(
        user_id=int(user.get("user_id") or user.get("id") or 0),
        first_name=user.get("first_name") or user.get("name"),
        is_dev=bool(launch.get("is_dev")),
    )


@router.post("/events", status_code=204, summary="Событие аналитики из мини-приложения")
async def post_event(
    body: TrackRequest,
    launch: Launch,
    session: Session,
) -> None:
    user = launch.get("user") or {}
    await track(session, user.get("user_id") or user.get("id"), body.name, body.props)
    await session.commit()
