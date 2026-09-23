"""T0 (#35): фоновый цикл планировщика в lifespan."""

import asyncio
import logging
from datetime import UTC

import pytest

from app import main
from app.calendar import reminders
from app.core.config import Settings


async def test_failed_tick_does_not_stop_loop(monkeypatch, caplog):
    calls: list = []
    enough = asyncio.Event()

    async def flaky_tick(now, max_client):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("тик сломался")
        if len(calls) >= 3:
            enough.set()

    monkeypatch.setattr(reminders, "tick", flaky_tick)
    caplog.set_level(logging.ERROR, logger="app.main")

    task = asyncio.create_task(main.scheduler_loop(object(), interval=0))
    await asyncio.wait_for(enough.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 3
    assert all(now.tzinfo is UTC for now in calls)
    assert any(r.exc_info and "тик сломался" in str(r.exc_info[1]) for r in caplog.records)


async def test_lifespan_starts_scheduler_and_shutdown_stops_it(monkeypatch):
    ticked = asyncio.Event()

    async def tick(now, max_client):
        ticked.set()

    monkeypatch.setattr(reminders, "tick", tick)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(_env_file=None))

    async with main.lifespan(main.app):
        task = main.app.state.scheduler_task
        assert task is not None
        await asyncio.wait_for(ticked.wait(), timeout=2)
        assert not task.done()

    assert task.cancelled()


async def test_scheduler_disabled_by_setting(monkeypatch):
    async def tick(now, max_client):
        raise AssertionError("цикл не должен запускаться")

    monkeypatch.setattr(reminders, "tick", tick)
    monkeypatch.setattr(
        main, "get_settings", lambda: Settings(_env_file=None, scheduler_enabled=False)
    )

    async with main.lifespan(main.app):
        assert main.app.state.scheduler_task is None
        await asyncio.sleep(0)


async def test_tick_stub_is_noop():
    """Пока T6 не сделана, тик ничего не делает и не шумит ошибками в лог."""
    from datetime import datetime

    assert await reminders.tick(datetime.now(UTC), object()) is None
