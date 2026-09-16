"""Модели базы данных. Меняет только техлид: от схемы зависят все дорожки."""

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, String
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
