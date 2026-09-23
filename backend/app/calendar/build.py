"""Сборка календаря пользователя по профилю (задача T4).

data-model.md §1–2, reminders.md «Когда создаются уведомления», экран 5, D16, D22.

Идентичность события при сборке — (obligation_id, original_date): если аналитик поправил
производственный календарь и due_date сдвинулся, это то же событие, а не новое. Уникальный
ключ в БД (user_id, obligation_id, due_date) — вторая линия защиты от дублей.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.dates import obligation_dates
from app.calendar.reminders import sync_obligation_notifications
from app.calendar.types import DueDate, Obligation, Reference
from app.core.models import Notification, Profile, UserObligation


@dataclass(frozen=True, slots=True)
class ProfileFacts:
    """То из Profile, от чего зависит состав календаря. None — ответа ещё нет."""

    regime: str | None
    income_band: str | None
    has_employees: bool | None
    nds_payer: bool | None
    timezone: str  # IANA


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Итог сборки для экрана 5."""

    created: int  # новых UserObligation этой сборкой; повторная сборка → 0
    total: int  # всего UserObligation пользователя в горизонте сборки
    this_year: int  # из них с due_date до 31 декабря текущего года — число в тексте экрана 5
    nearest_obligation_id: str | None  # ближайшее неотмеченное с due_date ≥ сегодня
    nearest_due_date: date | None


def applies(obligation: Obligation, facts: ProfileFacts) -> bool:
    """Попадает ли запись в календарь профиля (все условия applies_if через И).

    Условие не задано — подходит любой ответ. Условие задано, а ответа нет (None, D24) —
    запись не попадает: неизвестное не совпадает ни с чем (так же, как nds_payer в D25).
    """
    cond = obligation.applies_if
    if cond.regime is not None and facts.regime not in cond.regime:
        return False
    if cond.income_band is not None and facts.income_band not in cond.income_band:
        return False
    if cond.has_employees is not None and facts.has_employees is not cond.has_employees:
        return False
    return cond.nds_payer is None or facts.nds_payer is cond.nds_payer


def build_horizon(today: date) -> date:
    """Последний день сборки: 31 декабря следующего года (D22)."""
    return date(today.year + 1, 12, 31)


def _facts(profile: Profile) -> ProfileFacts:
    return ProfileFacts(
        regime=profile.regime,
        income_band=profile.income_band,
        has_employees=profile.has_employees,
        nds_payer=profile.nds_payer,
        timezone=profile.timezone,
    )


def _wanted(
    reference: Reference, facts: ProfileFacts, today: date, horizon: date
) -> dict[tuple[str, date], tuple[Obligation, DueDate]]:
    """Все события профиля с today ≤ due_date ≤ horizon, ключ — (obligation_id, original_date).

    Считается целиком до первой записи в БД: MissingYearError не оставит полусборку.
    """
    wanted: dict[tuple[str, date], tuple[Obligation, DueDate]] = {}
    for ob in reference.catalog.obligations:
        if not applies(ob, facts):
            continue
        for year in range(today.year, horizon.year + 1):
            for due in obligation_dates(ob, year, reference.workdays):
                if today <= due.due_date <= horizon:
                    wanted[(ob.id, due.original_date)] = (ob, due)
    return wanted


async def build_calendar(
    session: AsyncSession,
    user_id: int,
    *,
    now: datetime,
    reference: Reference,
) -> BuildResult:
    """Создаёт UserObligation от сегодняшнего дня пользователя до build_horizon и их Notification.

    - `now` — aware UTC; «сегодня» считается в Profile.timezone;
    - идемпотентно: ключ (user_id, obligation_id, due_date), повторный вызов дублей не создаёт;
    - уведомления с send_at в прошлом не создаются;
    - ставит Profile.calendar_built_at;
    - пишет в переданную сессию и не коммитит: коммит — на вызывающем (короткая транзакция,
      без сетевых вызовов внутри).

    Пересборка («Собрать заново», экраны 1, 19) — по ключу (obligation_id, original_date):
    - отмеченное (`done_at`) не трогается никогда и не дублируется, даже если срок сдвинулся;
    - неотмеченное, которое по-прежнему нужно, — due_date и rule_version обновляются на месте,
      уведомления пересинхронизируются;
    - неотмеченное, которое больше не подходит профилю или пропало из справочника, — удаляется
      со всеми уведомлениями; прошедшее неотмеченное, которое подходит, остаётся (просрочено).
    Профиля нет — LookupError. Ошибки справочника (MissingYearError) — наружу, до записи в БД.
    """
    profile = await session.get(Profile, user_id)
    if profile is None:
        raise LookupError(f"У пользователя {user_id} нет профиля — календарь не из чего собрать")

    facts = _facts(profile)
    today = now.astimezone(ZoneInfo(facts.timezone)).date()
    horizon = build_horizon(today)
    by_id = {ob.id: ob for ob in reference.catalog.obligations}
    wanted = _wanted(reference, facts, today, horizon)

    existing = list(
        await session.scalars(
            select(UserObligation)
            .where(UserObligation.user_id == user_id)
            .order_by(UserObligation.id)
        )
    )

    to_delete: list[UserObligation] = []
    to_sync: list[tuple[UserObligation, Obligation]] = []
    for uo in existing:
        key = (uo.obligation_id, uo.original_date)
        match = wanted.pop(key, None)
        if uo.done_at is not None:
            continue  # отметка не теряется и не дублируется никогда
        if match is not None:
            ob, due = match
            uo.due_date = due.due_date
            uo.rule_version = ob.rule_version
            to_sync.append((uo, ob))
            continue
        ob = by_id.get(uo.obligation_id)
        if ob is not None and applies(ob, facts) and uo.due_date < today:
            continue  # прошедшее и неотмеченное — просрочено, остаётся в календаре
        to_delete.append(uo)

    if to_delete:
        ids = [uo.id for uo in to_delete]
        # Все статусы, не только pending: SQLite без AUTOINCREMENT может отдать id новой записи.
        await session.execute(
            delete(Notification).where(
                Notification.item_type == "obligation", Notification.item_id.in_(ids)
            )
        )
        for uo in to_delete:
            await session.delete(uo)
        await session.flush()

    created = 0
    for (ob_id, _original), (ob, due) in sorted(wanted.items(), key=lambda kv: kv[1][1].due_date):
        uo = UserObligation(
            user_id=user_id,
            obligation_id=ob_id,
            rule_version=ob.rule_version,
            original_date=due.original_date,
            due_date=due.due_date,
        )
        session.add(uo)
        to_sync.append((uo, ob))
        created += 1
    await session.flush()

    for uo, ob in to_sync:
        await sync_obligation_notifications(
            session,
            uo,
            needs_prep=ob.needs_prep,
            tz=facts.timezone,
            settings=profile.reminders,
            now=now,
        )

    profile.calendar_built_at = now
    await session.flush()

    in_horizon = list(
        await session.scalars(
            select(UserObligation)
            .where(
                UserObligation.user_id == user_id,
                UserObligation.due_date >= today,
                UserObligation.due_date <= horizon,
            )
            .order_by(UserObligation.due_date, UserObligation.obligation_id)
        )
    )
    year_end = date(today.year, 12, 31)
    nearest = next((uo for uo in in_horizon if uo.done_at is None), None)
    return BuildResult(
        created=created,
        total=len(in_horizon),
        this_year=sum(1 for uo in in_horizon if uo.due_date <= year_end),
        nearest_obligation_id=nearest.obligation_id if nearest else None,
        nearest_due_date=nearest.due_date if nearest else None,
    )
