"""DTO API мини-приложения (docs/spec/data-model.md §3). Типы фронта — miniapp/src/types.ts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.calendar.timezones import TIMEZONES

ItemTypeParam = Literal["obligation", "task"]
CategoryOut = Literal["taxes", "contributions", "reports", "custom"]
StatusOut = Literal["done", "overdue", "today", "upcoming"]

# Экран 17: название до 60 символов, «Напомнить» — 4 варианта, «Время» — любое ЧЧ:ММ (#103).
TITLE_MAX_LEN = 60
REMIND_OFFSETS = (0, 1, 3, 7)
DEFAULT_REMIND_OFFSET = 1
DEFAULT_REMIND_HOUR = 10
DEFAULT_REMIND_MINUTE = 0
HOUR_DESCRIPTION = "Час напоминания по поясу пользователя: 0–23"
MINUTE_DESCRIPTION = "Минуты напоминания: 0–59"


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
    remind_minute: int | None = Field(default=None, description="У задачи всегда число: 0–59")


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


class TaskInput(BaseModel):
    """Создание своей задачи (экран 17)."""

    title: str = Field(description=f"1–{TITLE_MAX_LEN} символов после обрезки пробелов")
    due_date: date = Field(
        description="Можно в прошлом (D32): тогда напоминания нет, без `done` статус overdue"
    )
    remind_offset_days: int = Field(default=DEFAULT_REMIND_OFFSET, description="0 · 1 · 3 · 7")
    remind_hour: int = Field(default=DEFAULT_REMIND_HOUR, ge=0, le=23, description=HOUR_DESCRIPTION)
    remind_minute: int = Field(
        default=DEFAULT_REMIND_MINUTE, ge=0, le=59, description=MINUTE_DESCRIPTION
    )
    done: bool = Field(
        default=False,
        description="«Уже выполнено» (D32): задача создаётся с done_at = сейчас, без напоминания",
    )

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _check_title(v)

    @field_validator("remind_offset_days")
    @classmethod
    def _offset(cls, v: int) -> int:
        return _check_offset(v)


class TaskPatch(BaseModel):
    """Изменение задачи: передаются только меняемые поля."""

    title: str | None = None
    due_date: date | None = Field(
        default=None, description="Можно в прошлом (D32): тогда напоминания нет"
    )
    remind_offset_days: int | None = None
    remind_hour: int | None = Field(default=None, ge=0, le=23, description=HOUR_DESCRIPTION)
    remind_minute: int | None = Field(default=None, ge=0, le=59, description=MINUTE_DESCRIPTION)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        return None if v is None else _check_title(v)

    @field_validator("remind_offset_days")
    @classmethod
    def _offset(cls, v: int | None) -> int | None:
        return None if v is None else _check_offset(v)


class ReminderSettingsOut(BaseModel):
    """Настройки напоминаний (экран 13) поверх умолчаний. d1 выключить нельзя — его здесь нет."""

    d30: bool = Field(description="За 30 дней (только обязательства с подготовкой)")
    d7: bool = Field(description="За 7 дней")
    hour: int = Field(description="Час отправки по поясу пользователя: 9 · 10 · 18")
    digest: bool = Field(description="Сводка по понедельникам (экран 12)")


class ProfileSettingsInput(BaseModel):
    """Сохранение экрана 13. Все поля обязательны; d1 не передаётся — он всегда включён."""

    d30: bool
    d7: bool
    hour: Literal[9, 10, 18] = Field(description="9 · 10 · 18")
    digest: bool
    timezone: str = Field(
        description="IANA-пояс из списка вопроса 4 экрана 2",
        json_schema_extra={"enum": list(TIMEZONES)},
    )

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str) -> str:
        if v not in TIMEZONES:
            raise ValueError(f"timezone must be one of {TIMEZONES}")
        return v


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
    reminders: ReminderSettingsOut = Field(description="Настройки напоминаний (экран 13)")


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
    remind_hour: int | None = Field(
        default=None, description="Время из сообщения (0–23); null — времени в сообщении не было"
    )
    remind_minute: int | None = Field(default=None, description="0–59; null вместе с remind_hour")


class MeResponse(BaseModel):
    user_id: int
    first_name: str | None = None
    is_dev: bool = False
    start_param: str | None = None
    has_profile: bool = False
    profile: ProfileOut | None = None
    draft: TaskDraft | None = None
    draft_stale: bool = Field(
        default=False,
        description=(
            "Мини-апп открыт кнопкой черновика из старого сообщения (start_param "
            "task_draft_<id>, id не совпал с текущим черновиком или черновика уже нет): "
            "draft = null, форма 17 открывается пустой с тостом (#96)"
        ),
    )


# --- «Поделиться сроком» (SHARE) ------------------------------------------------------------


class ShareInput(BaseModel):
    """Своё событие, которым делятся: обязательство или задача."""

    item_type: ItemTypeParam
    item_id: int


class ShareOut(BaseModel):
    """Приглашение: код для `?startapp=share_<code>` и готовая ссылка."""

    code: str = Field(description="URL-safe, [A-Za-z0-9_-]")
    link: str | None = Field(
        description=(
            "https://max.ru/<MAX_BOT_USERNAME>?startapp=share_<code>; "
            "null — MAX_BOT_USERNAME не задан, мини-апп строит ссылку сам"
        )
    )
    title: str = Field(description="Название, которое получит получатель (до 60 символов)")
    due_date: date


class ShareInvite(BaseModel):
    """Что видит получатель. Кто отправил — не отдаём."""

    code: str
    item_type: ItemTypeParam = Field(description="Чем поделились: обязательство или задача")
    title: str
    due_date: date


class ShareAccepted(BaseModel):
    """Результат «Добавить»: задача получателя."""

    created: bool = Field(description="false — приглашение уже принято раньше, та же задача")
    task: ItemCard
