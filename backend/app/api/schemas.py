"""DTO API мини-приложения (docs/spec/data-model.md §3). Типы фронта — miniapp/src/types.ts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ItemTypeParam = Literal["obligation", "task"]
CategoryOut = Literal["taxes", "contributions", "reports", "custom"]
StatusOut = Literal["done", "overdue", "today", "upcoming"]

# Экран 17: название до 60 символов, «Напомнить» — 4 варианта, «Время напоминания» — 3.
TITLE_MAX_LEN = 60
REMIND_OFFSETS = (0, 1, 3, 7)
REMIND_HOURS = (9, 10, 18)
DEFAULT_REMIND_OFFSET = 1
DEFAULT_REMIND_HOUR = 10


class CalendarItem(BaseModel):
    """Событие календаря — обязательство или своя задача. Не путать с `Event` (аналитика)."""

    type: ItemTypeParam
    id: int
    title: str
    category: CategoryOut
    due_date: date = Field(description="После переноса на рабочий день")
    original_date: date = Field(description="По правилу, до переноса; у задачи = due_date")
    status: StatusOut = Field(description="Считает бэкенд в часовом поясе пользователя")
    done_at: datetime | None = None


class HowtoLinkOut(BaseModel):
    label: str
    url: str


class ItemCard(CalendarItem):
    """Карточка (экран 16). Поля не своего типа — null."""

    # только обязательство
    norm: str | None = None
    source_url: str | None = None
    howto_steps: list[str] | None = Field(
        default=None, description="Ровно три шага; {due_date} раскрыт в «28 октября»"
    )
    howto_link: HowtoLinkOut | None = None
    penalty_text: str | None = None
    last_checked_at: date | None = None
    # только задача
    remind_offset_days: int | None = None
    remind_hour: int | None = None


def _check_title(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("title_required")
    if len(value) > TITLE_MAX_LEN:
        raise ValueError("title_too_long")
    return value


def _check_offset(value: int) -> int:
    if value not in REMIND_OFFSETS:
        raise ValueError(f"remind_offset_days must be one of {REMIND_OFFSETS}")
    return value


def _check_hour(value: int) -> int:
    if value not in REMIND_HOURS:
        raise ValueError(f"remind_hour must be one of {REMIND_HOURS}")
    return value


class TaskInput(BaseModel):
    """Создание своей задачи (экран 17)."""

    title: str = Field(description=f"1–{TITLE_MAX_LEN} символов после обрезки пробелов")
    due_date: date = Field(description="Не раньше сегодняшнего дня пользователя")
    remind_offset_days: int = Field(default=DEFAULT_REMIND_OFFSET, description="0 · 1 · 3 · 7")
    remind_hour: int = Field(default=DEFAULT_REMIND_HOUR, description="9 · 10 · 18")

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _check_title(v)

    @field_validator("remind_offset_days")
    @classmethod
    def _offset(cls, v: int) -> int:
        return _check_offset(v)

    @field_validator("remind_hour")
    @classmethod
    def _hour(cls, v: int) -> int:
        return _check_hour(v)


class TaskPatch(BaseModel):
    """Изменение задачи: передаются только меняемые поля."""

    title: str | None = None
    due_date: date | None = Field(
        default=None, description="Новая дата не раньше сегодняшней; прежнюю можно оставить"
    )
    remind_offset_days: int | None = None
    remind_hour: int | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        return None if v is None else _check_title(v)

    @field_validator("remind_offset_days")
    @classmethod
    def _offset(cls, v: int | None) -> int | None:
        return None if v is None else _check_offset(v)

    @field_validator("remind_hour")
    @classmethod
    def _hour(cls, v: int | None) -> int | None:
        return None if v is None else _check_hour(v)


class ProfileOut(BaseModel):
    """Ответы онбординга без служебных полей. nds_payer = null — определить нельзя (D25)."""

    income_band: str
    regime: str
    has_employees: bool
    timezone: str
    nds_payer: bool | None
    calendar_built_at: datetime | None
    reference_checked_at: date | None = Field(
        default=None,
        description="Дата сверки справочника (version каталога); null — справочник недоступен",
    )


class RebuildResponse(BaseModel):
    """Итог пересборки календаря (экран 19)."""

    items_count: int = Field(description="Событий до конца текущего года — как на экране 5")
    nearest_due_date: date | None = Field(
        description="Ближайший неотмеченный срок с сегодняшнего дня"
    )
    profile: ProfileOut


class TaskDraft(BaseModel):
    """Черновик задачи из бота (DialogState.data["task_draft"], экран 9 → 17)."""

    title: str
    due_date: date


class MeResponse(BaseModel):
    user_id: int
    first_name: str | None = None
    is_dev: bool = False
    start_param: str | None = None
    has_profile: bool = False
    profile: ProfileOut | None = None
    draft: TaskDraft | None = None
