"""Контекст одного апдейта: кто написал, что прислал, как ответить, какое состояние диалога."""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.max_client import MaxClient
from app.core.models import DialogState

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    update: dict
    session: AsyncSession
    max: MaxClient
    update_type: str = ""
    user_id: int | None = None
    chat_id: int | None = None
    user_name: str | None = None
    text: str | None = None
    payload: str | None = None  # payload callback-кнопки или диплинка ?start=
    callback_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    outbox: list[dict] = field(default_factory=list)  # см. reply/send_outbox

    @classmethod
    def from_update(cls, update: dict, session: AsyncSession, max_client: MaxClient) -> "Ctx":
        """Разбор апдейта MAX. Структуры сверять с реальными апдейтами (CAPTURE_UPDATES=true)."""
        ctx = cls(update=update, session=session, max=max_client)
        ctx.update_type = update.get("update_type", "")
        if ctx.update_type == "message_created":
            msg = update.get("message") or {}
            sender = msg.get("sender") or {}
            ctx.user_id = sender.get("user_id")
            ctx.user_name = sender.get("name") or sender.get("first_name")
            ctx.chat_id = (msg.get("recipient") or {}).get("chat_id")
            ctx.text = (msg.get("body") or {}).get("text")
        elif ctx.update_type == "message_callback":
            cb = update.get("callback") or {}
            user = cb.get("user") or {}
            ctx.user_id = user.get("user_id")
            ctx.user_name = user.get("name") or user.get("first_name")
            ctx.payload = cb.get("payload")
            ctx.callback_id = cb.get("callback_id")
            ctx.chat_id = ((update.get("message") or {}).get("recipient") or {}).get("chat_id")
        elif ctx.update_type == "bot_started":
            user = update.get("user") or {}
            ctx.user_id = user.get("user_id")
            ctx.user_name = user.get("name") or user.get("first_name")
            ctx.chat_id = update.get("chat_id")
            ctx.payload = update.get("payload")
        return ctx

    async def reply(self, text: str, attachments: list[dict] | None = None) -> None:
        """Ставит сообщение в очередь: в сеть оно уйдёт после коммита транзакции.

        Отправлять прямо отсюда нельзя. SQLite допускает одного писателя, а запрос к MAX API
        живёт до 35 секунд (таймаут httpx) — дольше, чем busy_timeout базы (30 секунд). Отправка
        внутри открытой транзакции ставит остальные апдейты в очередь за блокировкой записи
        и в худшем случае снова даёт "database is locked". Очередь отправляет
        `dispatcher.process_update` после коммита, см. `send_outbox`.

        Метод остаётся асинхронным: обработчики вызывают `await ctx.reply(...)`, и менять
        их из-за буфера не нужно.
        """
        self.outbox.append({"text": text, "attachments": attachments})

    def drop_outbox(self) -> None:
        """Выбрасывает неотправленное: транзакция откатилась, сообщать не о чем."""
        self.outbox.clear()

    async def send_outbox(self) -> None:
        """Отправляет накопленное. Вызывать только когда транзакция уже закрыта.

        Ошибка отправки одного сообщения не мешает остальным: апдейт уже обработан,
        и повторять его целиком нельзя.
        """
        pending, self.outbox = self.outbox, []
        for message in pending:
            if self.user_id is not None:
                target = {"user_id": self.user_id}
            elif self.chat_id is not None:
                target = {"chat_id": self.chat_id}
            else:
                log.warning("нет адресата для ответа на %s", self.update_type)
                continue
            try:
                await self.max.send_message(
                    message["text"], attachments=message["attachments"], **target
                )
            except Exception:
                log.exception("не удалось отправить ответ на %s", self.update_type)

    async def track(self, name: str, props: dict | None = None) -> None:
        await events.track(self.session, self.user_id, name, props)

    # --- FSM ---
    async def _state_row(self) -> DialogState | None:
        if self.user_id is None:
            return None
        row = await self.session.get(DialogState, self.user_id)
        if row is None:
            row = DialogState(user_id=self.user_id, state=None, data={})
            self.session.add(row)
        return row

    async def get_state(self) -> tuple[str | None, dict]:
        row = await self._state_row()
        return (row.state, dict(row.data or {})) if row else (None, {})

    async def set_state(self, state: str | None, data: dict | None = None) -> None:
        row = await self._state_row()
        if row is not None:
            row.state = state
            row.data = data if data is not None else dict(row.data or {})
