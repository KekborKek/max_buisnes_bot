"""Календарь, карточка, отметка и «Неверный срок» (экраны 14, 15, 16; data-model.md §3).

Отметка и снятие — reminders.md, правило 1: отметка отменяет все pending-уведомления
события, снятие пересоздаёт те, что ещё в будущем. События аналитики пишет только бэкенд.

Доменная часть отметки (условный UPDATE, отмена и возврат уведомлений, «Неверный срок») —
app.calendar.marks, общая с ботом (bot/handlers/done.py). Здесь — только своё событие
пользователя, коды ответа и события аналитики с source="card" (DEBT-1, #67).
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_reference, current_time, current_user_id
from app.api.schemas import CalendarItem, ItemCard, ItemTypeParam
from app.calendar import marks
from app.calendar.types import Reference
from app.core.db import get_session
from app.core.events import track
from app.core.models import UserObligation

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(current_time)]
Ref = Annotated[Reference, Depends(current_reference)]

SOURCE = "card"  # отметка и жалоба из мини-аппа — только из карточки (экран 16)


@router.get(
    "/calendar",
    response_model=list[CalendarItem],
    summary="События за период и все просроченные без отметки",
)
async def get_calendar(
    user_id: UserId,
    session: Session,
    now: Now,
    reference: Ref,
    date_from: Annotated[date, Query(alias="from", description="YYYY-MM-DD, включительно")],
    date_to: Annotated[date, Query(alias="to", description="YYYY-MM-DD, включительно")],
) -> list[CalendarItem]:
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="from must not be later than to")
    return await service.calendar_items(
        session, user_id, date_from, date_to, reference=reference, now=now
    )


@router.get(
    "/items/{item_type}/{item_id}",
    response_model=ItemCard,
    summary="Карточка события",
    responses={404: {"description": "Нет такого события у пользователя"}},
)
async def get_item(
    item_type: ItemTypeParam,
    item_id: int,
    user_id: UserId,
    session: Session,
    now: Now,
    reference: Ref,
) -> ItemCard:
    return await service.item_card(
        session, user_id, item_type, item_id, reference=reference, now=now
    )


@router.post(
    "/items/{item_type}/{item_id}/done",
    response_model=ItemCard,
    summary="Отметить выполненным (идемпотентно)",
    responses={404: {"description": "Нет такого события у пользователя"}},
)
async def mark_done(
    item_type: ItemTypeParam,
    item_id: int,
    user_id: UserId,
    session: Session,
    now: Now,
    reference: Ref,
) -> ItemCard:
    item = await marks.load_own_item(
        session, reference, user_id=user_id, item_type=item_type, item_id=item_id
    )
    if item is None:
        raise service.not_found()
    profile = await service.load_profile(session, user_id)
    today = service.user_today(profile, now)

    # Условный UPDATE: из двух одновременных нажатий отметку ставит и событие пишет одно.
    if await marks.mark_done(session, item, now=now):
        await track(
            session,
            user_id,
            "item_done",
            {
                "item_id": item.item_id,
                "item_type": item.item_type,
                "source": SOURCE,
                "days_before_deadline": (item.due_date - today).days,
            },
        )
    await session.commit()
    return await service.item_card(
        session, user_id, item_type, item_id, reference=reference, now=now
    )


@router.delete(
    "/items/{item_type}/{item_id}/done",
    response_model=ItemCard,
    summary="Снять отметку (идемпотентно)",
    responses={404: {"description": "Нет такого события у пользователя"}},
)
async def unmark_done(
    item_type: ItemTypeParam,
    item_id: int,
    user_id: UserId,
    session: Session,
    now: Now,
    reference: Ref,
) -> ItemCard:
    item = await marks.load_own_item(
        session, reference, user_id=user_id, item_type=item_type, item_id=item_id
    )
    if item is None:
        raise service.not_found()
    profile = await service.load_profile(session, user_id)

    # Правило 1: снятие возвращает уведомления, у которых send_at ещё в будущем.
    if await marks.unmark_done(session, item, profile=profile, now=now):
        await track(
            session,
            user_id,
            "item_undone",
            {"item_id": item.item_id, "item_type": item.item_type, "source": SOURCE},
        )
    await session.commit()
    return await service.item_card(
        session, user_id, item_type, item_id, reference=reference, now=now
    )


@router.post(
    "/obligations/{item_id}/report",
    status_code=204,
    summary="«Неверный срок» по обязательству",
    responses={404: {"description": "Нет такого обязательства у пользователя"}},
)
async def report_wrong_date(
    item_id: int,
    user_id: UserId,
    session: Session,
    now: Now,
    reference: Ref,
) -> Response:
    item = await marks.load_own_item(
        session, reference, user_id=user_id, item_type="obligation", item_id=item_id
    )
    if item is None:
        raise service.not_found()
    assert isinstance(item.row, UserObligation)  # item_type="obligation" выше
    if await marks.report_wrong_date(session, user_id=user_id, uo=item.row, now=now):
        await track(
            session,
            user_id,
            "wrong_date_reported",
            {"item_id": item_id, "item_type": "obligation", "source": SOURCE},
        )
        await session.commit()
    return Response(status_code=204)
