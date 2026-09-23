"""Отметка «выполнено», её снятие и «Неверный срок» — общая доменная логика.

Кто зовёт: бот (экраны 7, 8). API мини-аппа (экран 16, `app/api/items.py`) пока держит свою
копию — следующий шаг перевести его сюда. Логика — reminders.md, правило 1: отметка отменяет все
pending-уведомления события, снятие пересоздаёт те, что ещё в будущем.

Функции пишут в сессию и не коммитят. События аналитики пишет вызывающий: у него `source`.
Сетевых вызовов здесь нет.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Date, DateTime, and_, exists, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import reminders as rem
from app.calendar.types import ItemType, Obligation, Reference
from app.core.models import Profile, Task, UserObligation, WrongDateReport


@dataclass(frozen=True, slots=True)
class OwnItem:
    """Событие календаря пользователя: строка БД и (у обязательства) запись справочника."""

    item_type: ItemType
    row: UserObligation | Task
    obligation: Obligation | None = None  # у задачи — None

    @property
    def item_id(self) -> int:
        return self.row.id

    @property
    def title(self) -> str:
        if self.obligation is not None:
            return self.obligation.title
        assert isinstance(self.row, Task)
        return self.row.title

    @property
    def due_date(self) -> date:
        return self.row.due_date

    @property
    def done_at(self) -> datetime | None:
        return self.row.done_at


async def load_own_item(
    session: AsyncSession,
    reference: Reference,
    *,
    user_id: int,
    item_type: str,
    item_id: int,
) -> OwnItem | None:
    """Своё событие (отмеченное тоже); чужое, удалённое или пропавшее из справочника — None."""
    if item_type == "obligation":
        uo = await session.get(UserObligation, item_id)
        if uo is None or uo.user_id != user_id:
            return None
        ob = next((o for o in reference.catalog.obligations if o.id == uo.obligation_id), None)
        if ob is None:
            return None
        return OwnItem(item_type="obligation", row=uo, obligation=ob)
    if item_type == "task":
        task = await session.get(Task, item_id)
        if task is None or task.user_id != user_id or task.deleted_at is not None:
            return None
        return OwnItem(item_type="task", row=task)
    return None


def _model(item: OwnItem) -> type[UserObligation] | type[Task]:
    return UserObligation if item.item_type == "obligation" else Task


async def mark_done(session: AsyncSession, item: OwnItem, *, now: datetime) -> bool:
    """Ставит `done_at = now`, если отметки ещё нет, и отменяет pending-уведомления.

    Условный UPDATE: из двух одновременных нажатий отметку ставит одно, второе получает False.
    Возвращает «изменилось ли». После вызова `item.done_at` актуален в обоих случаях.
    """
    model = _model(item)
    result = await session.execute(
        update(model)
        .where(model.id == item.item_id, model.done_at.is_(None))
        .values(done_at=now)
        .execution_options(synchronize_session=False)
    )
    changed = bool(result.rowcount)
    if changed:
        await rem.cancel_pending(session, item.item_type, item.item_id)
    await session.refresh(item.row)
    return changed


async def unmark_done(
    session: AsyncSession, item: OwnItem, *, profile: Profile | None, now: datetime
) -> bool:
    """Снимает отметку, если она есть, и возвращает уведомления с `send_at` в будущем.

    Возвращает «изменилось ли»; повторное снятие ничего не делает.
    """
    model = _model(item)
    result = await session.execute(
        update(model)
        .where(model.id == item.item_id, model.done_at.is_not(None))
        .values(done_at=None)
        .execution_options(synchronize_session=False)
    )
    await session.refresh(item.row)
    if not result.rowcount:
        return False
    _, tz = rem.user_today(profile, now)
    if isinstance(item.row, UserObligation):
        assert item.obligation is not None
        await rem.sync_obligation_notifications(
            session,
            item.row,
            needs_prep=item.obligation.needs_prep,
            tz=tz,
            settings=profile.reminders if profile is not None else None,
            now=now,
        )
    else:
        await rem.sync_task_notification(session, item.row, tz=tz, now=now)
    return True


async def report_wrong_date(
    session: AsyncSession, *, user_id: int, uo: UserObligation, now: datetime
) -> bool:
    """«Неверный срок»: одна запись `WrongDateReport` на (пользователь, обязательство, дата).

    Проверка и вставка — один оператор (INSERT … SELECT … WHERE NOT EXISTS): уникального
    ограничения в таблице нет, а так два быстрых нажатия не создадут две записи.
    Возвращает True, если запись создана сейчас.
    """
    already = exists().where(
        and_(
            WrongDateReport.user_id == user_id,
            WrongDateReport.obligation_id == uo.obligation_id,
            WrongDateReport.due_date == uo.due_date,
        )
    )
    row = select(
        literal(user_id),
        literal(uo.obligation_id),
        literal(uo.due_date, Date()),
        literal(now, DateTime(timezone=True)),
    ).where(~already)
    result = await session.execute(
        insert(WrongDateReport).from_select(
            ["user_id", "obligation_id", "due_date", "created_at"], row
        )
    )
    return bool(result.rowcount)
