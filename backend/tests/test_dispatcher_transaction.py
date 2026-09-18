"""Транзакция записи не держится на время запроса к MAX API.

SQLite допускает одного писателя в любом режиме журнала: WAL разводит читателей и писателя,
но не писателей между собой. Пока обработчик ждёт ответа MAX API внутри открытой транзакции,
остальные апдейты стоят за блокировкой записи — а таймаут httpx (35 с) больше busy_timeout
базы (30 с), так что очередь заканчивается "database is locked".

Тесты проверяют не «быстро ли работает», а порядок: коммит раньше отправки.
"""

import asyncio
import time

from sqlalchemy import func, select

from app.bot.dispatcher import process_update
from app.bot.router import router
from app.core.db import SessionLocal
from app.core.models import DialogState, Event, ProcessedUpdate
from app.core.texts import t
from tests.conftest import FakeMax, load_update

SEND_DELAY_SECONDS = 0.3
# на последовательной обработке двух апдейтов ушло бы ~2 задержки; порог с запасом на медленную
# машину, но заведомо ниже суммы
SERIAL_LIMIT_SECONDS = SEND_DELAY_SECONDS * 1.6


def _update_for(user_id: int) -> dict:
    """bot_started от отдельного пользователя: свой ключ идемпотентности."""
    update = load_update("bot_started")
    update["user"] = dict(update["user"], user_id=user_id)
    update["chat_id"] = user_id
    return update


class CommitCheckingMax(FakeMax):
    """В момент отправки заглядывает в базу отдельной сессией: видна ли запись обработчика."""

    def __init__(self) -> None:
        super().__init__()
        self.visible_at_send: list[tuple[int, int]] = []

    async def send_message(self, text, **kwargs):
        async with SessionLocal() as probe:
            events = await probe.scalar(select(func.count()).select_from(Event))
            states = await probe.scalar(select(func.count()).select_from(DialogState))
        self.visible_at_send.append((events, states))
        return await super().send_message(text, **kwargs)


class SlowMax(FakeMax):
    """MAX API, который отвечает не сразу."""

    async def send_message(self, text, **kwargs):
        await asyncio.sleep(SEND_DELAY_SECONDS)
        return await super().send_message(text, **kwargs)


async def test_commit_happens_before_send():
    """Ответ уходит в MAX только после коммита: на старом коде здесь были нули."""
    max_client = CommitCheckingMax()

    await process_update(load_update("bot_started"), max_client)

    assert len(max_client.sent) == 1
    events, states = max_client.visible_at_send[0]
    assert events == 1, "событие аналитики ещё не закоммичено — транзакция открыта при отправке"
    assert states == 1, "состояние FSM ещё не закоммичено — транзакция открыта при отправке"


async def test_parallel_updates_do_not_wait_for_each_other():
    """Два апдейта с медленной отправкой обрабатываются параллельно, а не по очереди."""
    max_client = SlowMax()
    updates = [_update_for(2000), _update_for(2001)]

    started = time.perf_counter()
    await asyncio.gather(*(process_update(u, max_client) for u in updates))
    elapsed = time.perf_counter() - started

    assert [m["text"] for m in max_client.sent] == [t("start.greeting")] * 2
    assert elapsed < SERIAL_LIMIT_SECONDS, (
        f"апдейты сериализовались: {elapsed:.2f} с при задержке отправки {SEND_DELAY_SECONDS} с"
    )


async def test_failed_handler_keeps_key_and_drops_queued_replies(fake_max, monkeypatch):
    """Обработчик упал: ключ остался, ответы обработчика не ушли, пользователь получил ошибку."""

    async def boom(ctx):
        await ctx.track("must_be_rolled_back")
        await ctx.reply("это сообщение не должно уйти")
        raise RuntimeError("обработчик упал")

    async def resolve(ctx):
        return boom

    monkeypatch.setattr(router, "resolve", resolve)

    update = load_update("bot_started")
    await process_update(update, fake_max)

    assert [m["text"] for m in fake_max.sent] == [t("errors.internal")]
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Event)) == 0
        assert await session.scalar(select(func.count()).select_from(ProcessedUpdate)) == 1

    # повторная доставка того же апдейта не обрабатывается заново (at-most-once)
    await process_update(update, fake_max)
    assert len(fake_max.sent) == 1
