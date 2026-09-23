"""Календарь, карточка, отметка и «Неверный срок» (экраны 14, 15, 16; data-model.md §3).

Отметка и снятие — reminders.md, правило 1: отметка отменяет все pending-уведомления
события, снятие пересоздаёт те, что ещё в будущем. События аналитики пишет только бэкенд.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_reference, current_time, current_user_id
from app.api.schemas import CalendarItem, ItemCard, ItemTypeParam
from app.calendar.reminders import (
    cancel_pending,
    sync_obligation_notifications,
    sync_task_notification,
)
from app.calendar.types import Reference
from app.core.db import get_session
from app.core.events import track
from app.core.models import Task, UserObligation, WrongDateReport

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
    profile = await service.load_profile(session, user_id)
    today = service.user_today(profile, now)
    if item_type == "obligation":
        uo, _ = await service.own_obligation(session, user_id, item_id, reference)
        model: type[UserObligation] | type[Task] = UserObligation
        item: UserObligation | Task = uo
    else:
        item = await service.own_task(session, user_id, item_id)
        model = Task

    # Условный UPDATE: из двух одновременных нажатий отметку ставит и событие пишет одно.
    result = await session.execute(
        update(model)
        .where(model.id == item_id, model.done_at.is_(None))
        .values(done_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        await cancel_pending(session, item_type, item_id)
        await track(
            session,
            user_id,
            "item_done",
            {
                "item_id": item_id,
                "item_type": item_type,
                "source": SOURCE,
                "days_before_deadline": (item.due_date - today).days,
            },
        )
    await session.commit()
    await session.refresh(item)
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
    profile = await service.load_profile(session, user_id)
    tz = service.user_tz(profile)
    if item_type == "obligation":
        uo, ob = await service.own_obligation(session, user_id, item_id, reference)
        model: type[UserObligation] | type[Task] = UserObligation
        item: UserObligation | Task = uo
    else:
        item = await service.own_task(session, user_id, item_id)
        model = Task

    result = await session.execute(
        update(model)
        .where(model.id == item_id, model.done_at.is_not(None))
        .values(done_at=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        await session.refresh(item)
        # Правило 1: вернуть уведомления, у которых send_at ещё в будущем.
        if isinstance(item, UserObligation):
            await sync_obligation_notifications(
                session,
                item,
                needs_prep=ob.needs_prep,
                tz=tz,
                settings=profile.reminders if profile is not None else None,
                now=now,
            )
        else:
            await sync_task_notification(session, item, tz=tz, now=now)
        await track(
            session,
            user_id,
            "item_undone",
            {"item_id": item_id, "item_type": item_type, "source": SOURCE},
        )
    await session.commit()
    await session.refresh(item)
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
    reference: Ref,
) -> Response:
    uo, _ = await service.own_obligation(session, user_id, item_id, reference)
    exists = await session.scalar(
        select(WrongDateReport.id).where(
            WrongDateReport.user_id == user_id,
            WrongDateReport.obligation_id == uo.obligation_id,
            WrongDateReport.due_date == uo.due_date,
        )
    )
    if exists is None:
        session.add(
            WrongDateReport(user_id=user_id, obligation_id=uo.obligation_id, due_date=uo.due_date)
        )
        await track(
            session,
            user_id,
            "wrong_date_reported",
            {"item_id": item_id, "item_type": "obligation", "source": SOURCE},
        )
        await session.commit()
    return Response(status_code=204)
