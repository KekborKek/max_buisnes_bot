"""Сборка календаря пользователя по профилю (задача T4). Сейчас — только контракт.

data-model.md §1–2, reminders.md «Когда создаются уведомления», экран 5, D16, D22.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.types import Obligation, Reference


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
    """Попадает ли запись в календарь профиля (все условия applies_if через И)."""
    raise NotImplementedError("T4")


def build_horizon(today: date) -> date:
    """Последний день сборки: 31 декабря следующего года (D22)."""
    raise NotImplementedError("T4")


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
    """
    raise NotImplementedError("T4")
