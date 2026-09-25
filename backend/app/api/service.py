"""Общая часть эндпоинтов календаря: поиск своих событий, сборка DTO, даты.

Статус — `app.calendar.status`; уведомления — `app.calendar.reminders`. Здесь только
чтение из БД и превращение строк в DTO, без коммитов.
"""

import logging
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CalendarItem,
    HowtoLinkOut,
    ItemCard,
    ItemTypeParam,
    ProfileOut,
    ReminderSettingsOut,
)
from app.calendar.digest import digest_enabled
from app.calendar.howto_text import expand_howto_steps
from app.calendar.reminders import as_utc, reminder_settings
from app.calendar.status import item_status, today_in
from app.calendar.types import Obligation, Reference
from app.core.models import DEFAULT_TIMEZONE, Profile, Task, User, UserObligation

log = logging.getLogger(__name__)

NOT_FOUND = "item not found"

# --- Пользователь и пояс ---------------------------------------------------------------------


async def load_profile(session: AsyncSession, user_id: int) -> Profile | None:
    return await session.get(Profile, user_id)


def user_tz(profile: Profile | None) -> str:
    """IANA-пояс профиля; нет профиля или пояс неизвестен — Europe/Moscow."""
    tz = profile.timezone if profile is not None and profile.timezone else DEFAULT_TIMEZONE
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TIMEZONE
    return tz


def user_today(profile: Profile | None, now: datetime) -> date:
    return today_in(user_tz(profile), now)


async def ensure_user(session: AsyncSession, user_id: int) -> None:
    """Строка User для внешнего ключа: мини-апп мог открыться раньше, чем бот увидел человека."""
    if await session.get(User, user_id) is None:
        session.add(User(user_id=user_id))
        await session.flush()


# --- Профиль (экраны 14, 18, 19) --------------------------------------------------------------


def reference_checked_at(reference: Reference | None) -> date | None:
    """Дата сверки справочника — `version` каталога, как на экране 11. Не дата — None."""
    if reference is None:
        return None
    try:
        return date.fromisoformat(reference.catalog.version)
    except ValueError:
        log.warning("version справочника не в формате YYYY-MM-DD: %r", reference.catalog.version)
        return None


def profile_complete(profile: Profile | None) -> bool:
    """Профиль заполнен, когда есть все ответы онбординга (D24)."""
    return (
        profile is not None
        and profile.income_band is not None
        and profile.regime is not None
        and profile.has_employees is not None
    )


def reminder_settings_out(raw: Mapping[str, Any] | None) -> ReminderSettingsOut:
    """Profile.reminders поверх умолчаний; `digest` — отдельно: `reminder_settings` его режет."""
    opts = reminder_settings(raw)
    return ReminderSettingsOut(
        d30=bool(opts["d30"]),
        d7=bool(opts["d7"]),
        hour=int(opts["hour"]),
        digest=digest_enabled(raw),
    )


def profile_out(profile: Profile | None, reference: Reference | None) -> ProfileOut | None:
    """Незаполненный профиль (D24) — None: для мини-аппа профиля нет."""
    if profile is None or not profile_complete(profile):
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
        reference_checked_at=reference_checked_at(reference),
        reminders=reminder_settings_out(profile.reminders),
    )


# --- Поиск своих событий ---------------------------------------------------------------------


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


async def own_obligation(
    session: AsyncSession, user_id: int, item_id: int, reference: Reference
) -> tuple[UserObligation, Obligation]:
    """Своё обязательство вместе с записью справочника; чужое или пропавшее из справочника — 404."""
    uo = await session.get(UserObligation, item_id)
    if uo is None or uo.user_id != user_id:
        raise not_found()
    ob = catalog_index(reference).get(uo.obligation_id)
    if ob is None:
        # Запись убрали из справочника; пересборка удалит событие, а пока показать нечего.
        raise not_found()
    return uo, ob


async def own_task(
    session: AsyncSession, user_id: int, item_id: int, *, include_deleted: bool = False
) -> Task:
    task = await session.get(Task, item_id)
    if task is None or task.user_id != user_id:
        raise not_found()
    if task.deleted_at is not None and not include_deleted:
        raise not_found()
    return task


def catalog_index(reference: Reference) -> dict[str, Obligation]:
    return {ob.id: ob for ob in reference.catalog.obligations}


# --- DTO ------------------------------------------------------------------------------------


def _done_at(value: datetime | None) -> datetime | None:
    return None if value is None else as_utc(value)


def obligation_item(uo: UserObligation, ob: Obligation, today: date) -> CalendarItem:
    return CalendarItem(
        type="obligation",
        id=uo.id,
        title=ob.title,
        category=ob.category,
        due_date=uo.due_date,
        original_date=uo.original_date,
        status=item_status(uo.due_date, uo.done_at, today),
        done_at=_done_at(uo.done_at),
    )


def task_item(task: Task, today: date) -> CalendarItem:
    return CalendarItem(
        type="task",
        id=task.id,
        title=task.title,
        category="custom",
        due_date=task.due_date,
        original_date=task.due_date,
        status=item_status(task.due_date, task.done_at, today),
        done_at=_done_at(task.done_at),
    )


def obligation_card(uo: UserObligation, ob: Obligation, today: date) -> ItemCard:
    return ItemCard(
        **obligation_item(uo, ob, today).model_dump(),
        norm=ob.norm,
        source_url=ob.source_url,
        howto_steps=expand_howto_steps(ob, uo.due_date, today),
        howto_link=HowtoLinkOut(label=ob.howto_link.label, url=ob.howto_link.url),
        penalty_text=ob.penalty_text,
        last_checked_at=ob.last_checked_at,
    )


def task_card(task: Task, today: date) -> ItemCard:
    return ItemCard(
        **task_item(task, today).model_dump(),
        remind_offset_days=task.remind_offset_days,
        remind_hour=task.remind_hour,
    )


async def item_card(
    session: AsyncSession,
    user_id: int,
    item_type: ItemTypeParam,
    item_id: int,
    *,
    reference: Reference,
    now: datetime,
) -> ItemCard:
    today = user_today(await load_profile(session, user_id), now)
    if item_type == "obligation":
        uo, ob = await own_obligation(session, user_id, item_id, reference)
        return obligation_card(uo, ob, today)
    return task_card(await own_task(session, user_id, item_id), today)


async def calendar_items(
    session: AsyncSession,
    user_id: int,
    date_from: date,
    date_to: date,
    *,
    reference: Reference,
    now: datetime,
) -> list[CalendarItem]:
    """События с from ≤ due_date ≤ to плюс все неотмеченные с due_date < сегодня."""
    today = user_today(await load_profile(session, user_id), now)
    by_id = catalog_index(reference)

    uos = await session.scalars(
        select(UserObligation).where(
            UserObligation.user_id == user_id,
            or_(
                UserObligation.due_date.between(date_from, date_to),
                and_(UserObligation.done_at.is_(None), UserObligation.due_date < today),
            ),
        )
    )
    tasks = await session.scalars(
        select(Task).where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            or_(
                Task.due_date.between(date_from, date_to),
                and_(Task.done_at.is_(None), Task.due_date < today),
            ),
        )
    )

    items: list[CalendarItem] = []
    for uo in uos:
        ob = by_id.get(uo.obligation_id)
        if ob is None:
            log.warning(
                "UserObligation %s: записи %s нет в справочнике, в календарь не попадает",
                uo.id,
                uo.obligation_id,
            )
            continue
        items.append(obligation_item(uo, ob, today))
    items.extend(task_item(t, today) for t in tasks)
    items.sort(key=lambda i: (i.due_date, i.type, i.id))
    return items
