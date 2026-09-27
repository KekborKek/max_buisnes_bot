"""Свои задачи из мини-аппа (экраны 16, 17). Одно `task`-уведомление на задачу (D11)."""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_time, current_user_id
from app.api.schemas import ItemCard, TaskInput, TaskPatch
from app.calendar.reminders import sync_task_notification
from app.core.db import get_session
from app.core.events import track
from app.core.models import Task

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(current_time)]

NOT_FOUND = {404: {"description": "Нет такой задачи у пользователя"}}


def _date_in_past(due_date: date) -> RequestValidationError:
    """Та же форма 422, что у ошибок pydantic: мини-апп показывает её у поля «Дата»."""
    return RequestValidationError(
        [
            {
                "type": "date_past",
                "loc": ("body", "due_date"),
                "msg": "due_date must not be earlier than today",
                "input": due_date.isoformat(),
            }
        ]
    )


@router.post("/tasks", response_model=ItemCard, summary="Создать свою задачу")
async def create_task(body: TaskInput, user_id: UserId, session: Session, now: Now) -> ItemCard:
    profile = await service.load_profile(session, user_id)
    tz = service.user_tz(profile)
    today = service.user_today(profile, now)
    if body.due_date < today:
        raise _date_in_past(body.due_date)

    await service.ensure_user(session, user_id)
    task = Task(
        user_id=user_id,
        title=body.title,
        due_date=body.due_date,
        remind_offset_days=body.remind_offset_days,
        remind_hour=body.remind_hour,
        remind_minute=body.remind_minute,
    )
    session.add(task)
    await session.flush()
    await sync_task_notification(session, task, tz=tz, now=now)
    await track(
        session,
        user_id,
        "task_created",
        {"source": "form", "remind_offset": body.remind_offset_days},
    )
    await session.commit()
    return service.task_card(task, today)


@router.patch(
    "/tasks/{task_id}", response_model=ItemCard, summary="Изменить задачу", responses=NOT_FOUND
)
async def update_task(
    task_id: int, body: TaskPatch, user_id: UserId, session: Session, now: Now
) -> ItemCard:
    profile = await service.load_profile(session, user_id)
    tz = service.user_tz(profile)
    today = service.user_today(profile, now)
    task = await service.own_task(session, user_id, task_id)

    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    new_date = changes.get("due_date")
    # Прежнюю дату в прошлом оставить можно (правка названия просроченной задачи), новую — нет.
    if new_date is not None and new_date != task.due_date and new_date < today:
        raise _date_in_past(new_date)
    for field, value in changes.items():
        setattr(task, field, value)
    # Старое уведомление → cancelled, новое по новым дате и времени (экран 17).
    await sync_task_notification(session, task, tz=tz, now=now)
    await session.commit()
    return service.task_card(task, today)


@router.delete(
    "/tasks/{task_id}",
    status_code=204,
    summary="Удалить задачу (мягко; повторно — тоже 204)",
    responses=NOT_FOUND,
)
async def delete_task(task_id: int, user_id: UserId, session: Session, now: Now) -> Response:
    profile = await service.load_profile(session, user_id)
    task = await service.own_task(session, user_id, task_id, include_deleted=True)
    if task.deleted_at is None:
        task.deleted_at = now
        # deleted_at задан → все pending по задаче отменяются.
        await sync_task_notification(session, task, tz=service.user_tz(profile), now=now)
        await session.commit()
    return Response(status_code=204)
