"""«Пересобрать» на экране 19 (docs/screens/should-12-13-19.md).

Сборка — `app.calendar.build.build_calendar`, та же, что у кнопки «Собрать календарь» в боте
(экран 5). Она не трогает отметки, свои задачи, часовой пояс и настройки уведомлений и
идемпотентна. Здесь — транзакция, коды ответа и событие `calendar_built` с `rebuild`.
"""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_reference, current_time, current_user_id
from app.api.schemas import RebuildResponse
from app.calendar.build import BuildResult, build_calendar
from app.calendar.reminders import as_utc
from app.calendar.types import MissingYearError, Reference
from app.core.db import get_session
from app.core.events import track
from app.core.models import Profile

log = logging.getLogger(__name__)

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(current_time)]
Ref = Annotated[Reference, Depends(current_reference)]

PROFILE_INCOMPLETE = "profile_incomplete"
REFERENCE_UNAVAILABLE = "reference_unavailable"


async def _build(
    session: AsyncSession, user_id: int, *, now: datetime, reference: Reference
) -> tuple[Profile, BuildResult, bool]:
    """Сборка в транзакции сессии. Профиль не заполнен — 409 до первой записи."""
    profile = await service.load_profile(session, user_id)
    if profile is None or not service.profile_complete(profile):
        raise HTTPException(status_code=409, detail=PROFILE_INCOMPLETE)
    rebuild = profile.calendar_built_at is not None
    result = await build_calendar(session, user_id, now=now, reference=reference)
    return profile, result, rebuild


@router.post(
    "/calendar/rebuild",
    response_model=RebuildResponse,
    summary="Пересобрать календарь по профилю (экран 19, идемпотентно)",
    description=(
        "Отметки, свои задачи, часовой пояс и настройки уведомлений не сбрасываются. "
        "Повторный вызов дублей не создаёт. Пишет событие calendar_built с rebuild."
    ),
    responses={
        409: {
            "description": "Профиль не заполнен: календарь не из чего собрать (profile_incomplete)"
        },
        503: {"description": "В справочнике нет нужного года (reference_unavailable)"},
    },
)
async def rebuild_calendar(
    user_id: UserId,
    session: Session,
    now: Now,
    reference: Ref,
) -> RebuildResponse:
    try:
        try:
            profile, result, rebuild = await _build(session, user_id, now=now, reference=reference)
        except IntegrityError:
            # Два нажатия одновременно: второе упёрлось в уникальный ключ (user_id,
            # obligation_id, due_date). Строки первого уже в базе — повтор их просто найдёт.
            await session.rollback()
            log.info("пересборка %s: гонка на уникальном ключе, повторяю", user_id)
            profile, result, rebuild = await _build(session, user_id, now=now, reference=reference)
    except MissingYearError as exc:
        # Бросается до первой записи в БД — полусборки нет.
        await session.rollback()
        log.error("пересборка %s: %s", user_id, exc)
        await track(session, user_id, "error", {"where": "rebuild", "kind": "missing_year"})
        await session.commit()
        raise HTTPException(status_code=503, detail=REFERENCE_UNAVAILABLE) from exc

    seconds = int((now - as_utc(profile.started_at)).total_seconds())
    await track(
        session,
        user_id,
        "calendar_built",
        {"items_count": result.this_year, "seconds_since_start": seconds, "rebuild": rebuild},
    )
    # DTO — до коммита: после него атрибуты профиля истекают, а ленивой загрузки в async нет.
    out = service.profile_out(profile, reference)
    assert out is not None  # профиль заполнен — проверено в _build
    await session.commit()
    return RebuildResponse(
        items_count=result.this_year,
        nearest_due_date=result.nearest_due_date,
        profile=out,
    )
