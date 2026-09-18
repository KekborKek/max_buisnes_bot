from sqlalchemy import func, select

from app.bot.dispatcher import process_update
from app.bot.router import router
from app.core.db import SessionLocal
from app.core.models import Event
from app.core.texts import t
from tests.conftest import load_update


async def test_bot_started_sends_greeting_with_menu(fake_max):
    await process_update(load_update("bot_started"), fake_max)
    assert len(fake_max.sent) == 1
    assert fake_max.sent[0]["attachments"][0]["type"] == "inline_keyboard"


async def test_duplicate_update_processed_once(fake_max):
    update = load_update("bot_started")
    await process_update(update, fake_max)
    await process_update(update, fake_max)
    assert len(fake_max.sent) == 1


async def test_fsm_ask_name_scenario(fake_max):
    await process_update(load_update("callback_ask_name"), fake_max)
    await process_update(load_update("message_created"), fake_max)
    assert "Борис" in fake_max.sent[-1]["text"]
    async with SessionLocal() as s:
        n = await s.scalar(
            select(func.count()).select_from(Event).where(Event.name == "scenario_completed")
        )
    assert n == 1


async def test_group_message_is_answered_in_chat_not_in_direct(fake_max):
    """Ответ на сообщение из группового чата уходит в chat_id: обсуждение видят все."""
    await process_update(load_update("message_created_group"), fake_max)

    assert len(fake_max.sent) == 1
    assert fake_max.sent[0]["chat_id"] == 7001
    assert fake_max.sent[0]["user_id"] is None


async def test_dialog_message_is_answered_in_direct(fake_max):
    """В диалоге адресат прежний — user_id."""
    await process_update(load_update("message_created"), fake_max)

    assert len(fake_max.sent) == 1
    assert fake_max.sent[0]["user_id"] == 42
    assert fake_max.sent[0]["chat_id"] is None


async def test_callback_is_answered_without_handler_doing_it(fake_max):
    """Кнопка закрывается диспетчером: обработчик menu:ask_name сам answer_callback не зовёт."""
    await process_update(load_update("callback_ask_name"), fake_max)

    assert fake_max.answered == ["cb-123"]


async def test_callback_is_answered_even_if_handler_fails(fake_max, monkeypatch):
    """Спиннер гаснет до обработчика, поэтому его падение кнопку не подвешивает."""

    async def boom(ctx):
        raise RuntimeError("обработчик упал")

    async def resolve(ctx):
        return boom

    monkeypatch.setattr(router, "resolve", resolve)

    await process_update(load_update("callback_ask_name"), fake_max)

    assert fake_max.answered == ["cb-123"]
    assert [m["text"] for m in fake_max.sent] == [t("errors.internal")]


async def test_bot_stopped_tracks_event_and_stays_silent(fake_max):
    """bot_stopped — метрика оттока: событие пишем, пользователю не пишем."""
    await process_update(load_update("bot_stopped"), fake_max)

    assert fake_max.sent == []
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(Event.name, Event.user_id, Event.props).order_by(Event.id))
        ).all()
    assert [(r.name, r.user_id) for r in rows] == [("bot_stopped", 42)]
    assert rows[0].props["chat_id"] == 1001


async def test_bot_added_and_removed_track_events(fake_max):
    """bot_added и bot_removed тоже не теряются в fallback."""
    for timestamp, update_type in enumerate(("bot_added", "bot_removed"), start=1758001):
        update = dict(
            load_update("bot_stopped"), update_type=update_type, chat_id=7001, timestamp=timestamp
        )
        await process_update(update, fake_max)

    assert fake_max.sent == []
    async with SessionLocal() as session:
        names = (await session.execute(select(Event.name).order_by(Event.id))).scalars().all()
    assert names == ["bot_added", "bot_removed"]


async def test_callback_is_answered_again_on_redelivery(fake_max):
    """MAX повторяет доставку как раз когда первый ответ не дошёл — кнопку гасим снова.

    Обработчик при этом второй раз не запускается: идемпотентность не нарушена.
    """
    update = load_update("callback_ask_name")
    await process_update(update, fake_max)
    await process_update(update, fake_max)

    assert fake_max.answered == ["cb-123", "cb-123"]
    assert len(fake_max.sent) == 1
