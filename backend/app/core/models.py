"""Модели базы данных. Меняет только техлид: от схемы зависят все дорожки."""

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # id пользователя в MAX
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))  # только после request_contact
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DialogState(Base):
    """Состояние диалога (FSM). Хранится в БД, а не в памяти, чтобы переживать перезапуск."""

    __tablename__ = "dialog_states"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessedUpdate(Base):
    """Ключи уже обработанных апдейтов: вебхук может прислать одно событие повторно."""

    __tablename__ = "processed_updates"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """Продуктовая аналитика: каждое значимое действие пользователя."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Календарь обязательств (docs/spec/data-model.md §2) ---------------------------------
# Имя Event* занято аналитикой выше. Сущности календаря — Obligation*/Task/Notification.
# Все таблицы новые: существующий create_all добавляет их и в базу с данными.

DEFAULT_TIMEZONE = "Europe/Moscow"


def default_reminder_settings() -> dict:
    """Настройки напоминаний экрана 13 по умолчанию (reminders.md): 30/7 дней, 10:00."""
    return {"d30": True, "d7": True, "hour": 10}


class Profile(Base):
    """Профиль из ответов онбординга, 1:1 с User.

    Строка создаётся на первом /start (нужен started_at для метрики времени), ответы
    приходят позже — поэтому поля ответов допускают NULL (D24).
    """

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    # lt10 | 10_20 | 20_60 | gt60 | unknown
    income_band: Mapped[str | None] = mapped_column(String(16))
    # usn6 | usn15 | patent | ausn | unknown
    regime: Mapped[str | None] = mapped_column(String(16))
    has_employees: Mapped[bool | None] = mapped_column(Boolean)
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE)  # IANA
    # вычисляется при сохранении из income_band и nds.yaml; None — определить нельзя
    nds_payer: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    calendar_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders: Mapped[dict] = mapped_column(JSON, default=default_reminder_settings)


class UserObligation(Base):
    """Обязательство из справочника в календаре конкретного пользователя."""

    __tablename__ = "user_obligations"
    __table_args__ = (
        # повторная сборка календаря не плодит дубли
        UniqueConstraint("user_id", "obligation_id", "due_date", name="uq_user_obligation"),
        Index("ix_user_obligations_user_due", "user_id", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    obligation_id: Mapped[str] = mapped_column(String(64))  # id записи obligations.yaml
    rule_version: Mapped[int] = mapped_column(Integer)
    original_date: Mapped[date] = mapped_column(Date)  # дата по правилу
    due_date: Mapped[date] = mapped_column(Date)  # после переноса на рабочий день
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    """Своя задача пользователя. Удаление мягкое — через deleted_at."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_due", "user_id", "due_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(60))
    due_date: Mapped[date] = mapped_column(Date)
    remind_offset_days: Mapped[int] = mapped_column(Integer, default=1)  # 0 | 1 | 3 | 7
    remind_hour: Mapped[int] = mapped_column(Integer, default=10)  # 0–23
    # 0–59; server_default — лёгкая миграция добавит колонку в старую базу со значением 0 (#103)
    remind_minute: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """Запланированное сообщение (reminders.md). send_at — в UTC."""

    __tablename__ = "notifications"
    __table_args__ = (
        # выборка планировщика: pending с send_at <= now
        Index("ix_notifications_status_send_at", "status", "send_at"),
        # отмена всех уведомлений события при отметке
        Index("ix_notifications_item", "item_type", "item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    item_type: Mapped[str] = mapped_column(String(16))  # obligation | task
    item_id: Mapped[int] = mapped_column(Integer)  # UserObligation.id или Task.id
    kind: Mapped[str] = mapped_column(String(16))  # d30 | d7 | d1 | overdue | snooze | task
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # pending | sent | cancelled | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WrongDateReport(Base):
    """Кнопка «Неверный срок». Просмотр — прямым запросом к базе, интерфейса нет."""

    __tablename__ = "wrong_date_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    obligation_id: Mapped[str] = mapped_column(String(64))
    due_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- «Поделиться сроком» (SHARE) --------------------------------------------------------------
# Новые таблицы: create_all добавляет их в базу с данными, существующие не меняются.


class SharedItem(Base):
    """Приглашение «добавить срок себе в календарь»: ссылка `?startapp=share_<code>`.

    Снимок названия и даты на момент «Поделиться»: получатель видит то, что ему отправили,
    даже если отправитель потом перенёс или удалил событие. Имя отправителя не храним и не отдаём.
    """

    __tablename__ = "shared_items"
    __table_args__ = (
        # повторное «Поделиться» тем же событием с теми же названием и датой — тот же код
        Index("ix_shared_items_source", "from_user_id", "kind", "source_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # URL-safe [A-Za-z0-9_-] — алфавит startapp (dev.max.ru/docs/webapps/introduction)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    from_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))  # obligation | task
    source_item_id: Mapped[int] = mapped_column(
        Integer
    )  # UserObligation.id или Task.id — аналитика
    title: Mapped[str] = mapped_column(String(60))  # как Task.title: получателю создаётся задача
    due_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SharedItemAccept(Base):
    """Кто принял приглашение и какая задача создана: повторное принятие не плодит дублей."""

    __tablename__ = "shared_item_accepts"
    __table_args__ = (UniqueConstraint("share_id", "user_id", name="uq_shared_item_accept"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    share_id: Mapped[int] = mapped_column(ForeignKey("shared_items.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id", ondelete="CASCADE")
    )
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
