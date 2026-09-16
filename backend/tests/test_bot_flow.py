from sqlalchemy import func, select

from app.bot.dispatcher import process_update
from app.core.db import SessionLocal
from app.core.models import Event
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
