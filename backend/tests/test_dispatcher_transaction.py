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
# Порог считаем от времени одного апдейта, замеренного здесь же: абсолютные секунды на
# загруженной CI-машине мигают, а отношение «два параллельно ≈ один» устойчиво.
PARALLEL_OVERHEAD = 1.6


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

    started = time.perf_counter()
    await process_update(_update_for(2000), max_client)
    baseline = time.perf_counter() - started

    started = time.perf_counter()
    await asyncio.gather(
        *(process_update(u, max_client) for u in (_update_for(2001), _update_for(2002)))
    )
    elapsed = time.perf_counter() - started

    assert [m["text"] for m in max_client.sent] == [t("start.greeting")] * 3
    assert elapsed < baseline * PARALLEL_OVERHEAD, (
        f"апдейты сериализовались: {elapsed:.2f} с на двоих при {baseline:.2f} с на одного"
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


class BrokenMax(FakeMax):
    """MAX API, который не принимает сообщения."""

    async def send_message(self, text, **kwargs):
        raise RuntimeError("MAX недоступен")


async def test_send_failure_is_visible_in_analytics():
    """Отправка после коммита не откатывает работу обработчика — провал должен быть измерим."""
    max_client = BrokenMax()

    await process_update(load_update("bot_started"), max_client)

    async with SessionLocal() as session:
        names = (await session.execute(select(Event.name).order_by(Event.id))).scalars().all()
    # событие обработчика осталось (транзакция уже закоммичена), а провал отправки записан
    assert names == ["bot_started", "reply_send_failed"]
