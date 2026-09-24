"""«Пересобрать» на экране 19 (docs/screens/should-12-13-19.md).

Сборка — `app.calendar.build.build_calendar`, та же, что у кнопки «Собрать календарь» в боте
(экран 5). Она не трогает отметки, свои задачи, часовой пояс и настройки уведомлений и
идемпотентна. Здесь — транзакция, коды ответа и событие `calendar_built` с `rebuild`.
"""

import logging
from datetime import datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_time, current_user_id, optional_reference
from app.api.schemas import RebuildResponse
from app.calendar.build import build_calendar
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
# Справочник — без 500 из зависимости: битый файл (None) обрабатываем сами, как бот (экран 5).
OptionalRef = Annotated[Reference | None, Depends(optional_reference)]

PROFILE_INCOMPLETE = "profile_incomplete"
REFERENCE_UNAVAILABLE = "reference_unavailable"


async def _complete_profile(session: AsyncSession, user_id: int) -> Profile:
    """Профиль с ответами онбординга (D24). Не заполнен — 409 до первой записи в БД."""
    profile = await service.load_profile(session, user_id)
    if profile is None or not service.profile_complete(profile):
        raise HTTPException(status_code=409, detail=PROFILE_INCOMPLETE)
    return profile


async def _reference_error(
    session: AsyncSession, user_id: int, kind: str, exc: Exception | None = None
) -> NoReturn:
    """Справочник не дал собрать календарь — как бот (экран 5): событие error и 503.

    Незакоммиченное (полусборки не бывает: ошибки справочника — до первой записи) откатываем,
    событие пишем отдельным коммитом.
    """
    await session.rollback()
    await track(session, user_id, "error", {"where": "rebuild", "kind": kind})
    await session.commit()
    raise HTTPException(status_code=503, detail=REFERENCE_UNAVAILABLE) from exc


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
        503: {
            "description": (
                "Справочник недоступен: файл не загрузился или в нём нет нужного года "
                "(reference_unavailable)"
            )
        },
    },
)
async def rebuild_calendar(
    user_id: UserId,
    session: Session,
    now: Now,
    reference: OptionalRef,
) -> RebuildResponse:
    profile = await _complete_profile(session, user_id)
    # Флаг — до сборки и один раз на запрос: повтор после гонки не превращает первую
    # сборку в пересборку, даже если соседний запрос уже проставил calendar_built_at.
    rebuild = profile.calendar_built_at is not None
    if reference is None:
        # Файл справочника не загрузился: ReferenceFileError, причина — в логе optional_reference.
        await _reference_error(session, user_id, "reference_file")
    try:
        try:
            result = await build_calendar(session, user_id, now=now, reference=reference)
        except IntegrityError:
            # Два нажатия одновременно: второе упёрлось в уникальный ключ (user_id,
            # obligation_id, due_date). Строки первого уже в базе — повтор их просто найдёт.
            await session.rollback()
            log.info("пересборка %s: гонка на уникальном ключе, повторяю", user_id)
            result = await build_calendar(session, user_id, now=now, reference=reference)
            # После отката объекты сессии истекли — профиль читаем заново.
            profile = await _complete_profile(session, user_id)
    except MissingYearError as exc:
        log.error("пересборка %s: %s", user_id, exc)
        await _reference_error(session, user_id, "missing_year", exc)

    seconds = int((now - as_utc(profile.started_at)).total_seconds())
    await track(
        session,
        user_id,
        "calendar_built",
        {"items_count": result.this_year, "seconds_since_start": seconds, "rebuild": rebuild},
    )
    out = service.profile_out(profile, reference)
    assert out is not None  # профиль заполнен — проверено в _complete_profile
    await session.commit()
    return RebuildResponse(
        items_count=result.this_year,
        nearest_due_date=result.nearest_due_date,
        profile=out,
    )
