"""T13b (#72): демо-команда `/demo_remind <kind>` и аддитивная правка router.py (`on_command`).

Правка `router.py` — выход за «Можно менять» задачи, разрешена планировщиком (см. PR): роутер
раньше не умел ловить команду с аргументом (`on_text` сравнивает текст целиком), и без этого
демо не может показать напоминание нужного вида по команде `/demo_remind d7`.

Справочник — тестовые фикстуры (`backend/tests/fixtures/obligations.yaml`), «сейчас» и
онбординг — как в test_task_chat (23.09.2026 12:00 МСК, среда).
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.bot.context import Ctx
from app.bot.dispatcher import process_update
from app.bot.handlers import common, demo
from app.bot.router import Router
from app.calendar import reminders as rem
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import Notification, UserObligation
from app.core.texts import t
from tests.test_task_chat import NOW, TODAY, USER_ID, events, labels, onboarded, press, write

pytestmark = pytest.mark.usefixtures("fixture_reference")

YEARLY_ID = "test_yearly"  # needs_prep: true, penalty_text задан (фикстура)
YEARLY_TITLE = "Тестовое годовое обязательство"
YEARLY_PENALTY = "Тестовый текст о пене."
DUE = date(2026, 10, 28)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(common, "now", lambda: NOW)


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", [USER_ID])


@pytest.fixture
def no_support(monkeypatch):
    monkeypatch.setattr(get_settings(), "support_url", "")


@pytest.fixture
def no_bot_name(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_bot_username", "")


async def _obligation(
    *, ob: str = YEARLY_ID, due: date = DUE, user_id: int = USER_ID, done: bool = False
) -> int:
    """Добавляет `UserObligation`. Пользователя и профиль создаёт `onboarded()` — зови её раньше."""
    async with SessionLocal() as session:
        uo = UserObligation(
            user_id=user_id,
            obligation_id=ob,
            rule_version=1,
            original_date=due,
            due_date=due,
            done_at=NOW if done else None,
        )
        session.add(uo)
        await session.commit()
        return uo.id


async def _notifications() -> list[Notification]:
    async with SessionLocal() as session:
        return list((await session.scalars(select(Notification))).all())


def _expected(kind: str) -> tuple[str, list[dict] | None]:
    item = rem.ReminderItem(
        notification_id=0,
        item_type="obligation",
        item_id=1,  # id самого события в тексте/кнопках не участвует напрямую (только в payload)
        title=YEARLY_TITLE,
        due_date=DUE,
        original_date=DUE,
        penalty_text=YEARLY_PENALTY,
    )
    return rem.render_reminder(kind, [item], TODAY)


# --- админ: каждый вид ------------------------------------------------------------------------


@pytest.mark.parametrize("kind", demo.KINDS)
async def test_admin_gets_reminder_of_each_kind(fake_max, admin, no_bot_name, kind):
    await onboarded()
    item_id = await _obligation()

    await process_update(write(f"/demo_remind {kind}"), fake_max)

    msg = fake_max.sent[-1]
    expected_text, _ = _expected(kind)
    # Кнопки ссылаются на конкретный item_id — сверяем текст отдельно, кнопки — с поправкой на id.
    assert msg["text"] == expected_text
    assert msg["attachments"] == rem.reminder_keyboard(
        kind, "obligation", item_id, with_snooze=True
    )
    assert await _notifications() == []  # Notification не создаём (issue #72, п.1)


async def test_demo_reminder_case_insensitive_kind(fake_max, admin, no_bot_name):
    await onboarded()
    await _obligation()

    await process_update(write("/DEMO_REMIND D7"), fake_max)

    assert fake_max.sent[-1]["text"] == _expected("d7")[0]


# --- админ: подсказка и пустой календарь ------------------------------------------------------


@pytest.mark.parametrize("text", ["/demo_remind", "/demo_remind abracadabra"])
async def test_admin_missing_or_unknown_kind_shows_usage(fake_max, admin, text):
    await onboarded()

    await process_update(write(text), fake_max)

    assert fake_max.sent[-1]["text"] == t("demo.usage", kinds=", ".join(demo.KINDS))
    assert await _notifications() == []


async def test_admin_without_undone_obligations_gets_no_items(fake_max, admin):
    await onboarded()  # календарь не собран — обязательств нет

    await process_update(write("/demo_remind d7"), fake_max)

    assert fake_max.sent[-1]["text"] == t("demo.no_items")


async def test_admin_with_only_done_obligation_gets_no_items(fake_max, admin):
    await onboarded()
    await _obligation(done=True)

    await process_update(write("/demo_remind d7"), fake_max)

    assert fake_max.sent[-1]["text"] == t("demo.no_items")


# --- не-админ: неотличимо от «не понял» ----------------------------------------------------


async def test_non_admin_command_is_indistinguishable_from_unknown_text(
    fake_max, no_bot_name, no_support
):
    """Не-админ: /demo_remind как будто не существует — ровно тот же ответ, что и на «привет»."""
    await onboarded()
    await process_update(write("привет"), fake_max)
    plain = fake_max.sent[-1]

    await process_update(write("/demo_remind d7"), fake_max)
    from_command = fake_max.sent[-1]

    assert from_command == plain
    assert from_command["text"] == t("fallback.unknown")
    assert labels(from_command) == [[t("fallback.btn_about")]]


async def test_non_admin_with_calendar_still_gets_unknown(fake_max, no_bot_name, no_support):
    """Не-админ с собранным календарём — тот же экран 10, календарь тут ни при чём."""
    await onboarded()
    await _obligation(user_id=USER_ID)

    await process_update(write("/demo_remind d7"), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")


# --- кнопки демо-сообщения работают без Notification -----------------------------------------


async def test_done_button_on_demo_reminder_marks_item(fake_max, admin, no_bot_name):
    await onboarded()
    item_id = await _obligation()
    await process_update(write("/demo_remind d7"), fake_max)

    await process_update(press(f"r:done:obligation:{item_id}"), fake_max)

    async with SessionLocal() as session:
        uo = await session.get(UserObligation, item_id)
        assert uo.done_at is not None
    assert [name for name, _ in await events()][-1] == "item_done"


async def test_snooze_button_on_demo_reminder_creates_notification(fake_max, admin, no_bot_name):
    await onboarded()
    item_id = await _obligation()
    await process_update(write("/demo_remind d7"), fake_max)

    await process_update(press(f"r:snooze:obligation:{item_id}"), fake_max)

    notifications = await _notifications()
    assert [n.kind for n in notifications] == ["snooze"]
    assert fake_max.sent[-1]["text"] == t("reminder.snooze_ok", hour=10)
    assert fake_max.edited  # кнопка «Напомнить завтра» убрана из демо-сообщения


# --- router.on_command: юнит-тесты механизма (условие планировщика к разрешению на router.py) --


def _ctx(text: str | None, update_type: str = "message_created") -> Ctx:
    return Ctx(update={}, session=None, max=None, update_type=update_type, text=text)


async def test_on_command_finds_handler_with_argument():
    router = Router()

    async def handler(ctx: Ctx) -> None:
        pass

    router.on_command("/demo_remind")(handler)

    assert await router.resolve(_ctx("/demo_remind d7")) is handler


async def test_on_command_finds_handler_without_argument():
    router = Router()

    async def handler(ctx: Ctx) -> None:
        pass

    router.on_command("/demo_remind")(handler)

    assert await router.resolve(_ctx("/demo_remind")) is handler


async def test_on_command_is_case_insensitive():
    router = Router()

    async def handler(ctx: Ctx) -> None:
        pass

    router.on_command("/demo_remind")(handler)

    assert await router.resolve(_ctx("/DEMO_REMIND D7")) is handler


async def test_on_text_still_has_priority_over_on_command():
    router = Router()

    async def exact(ctx: Ctx) -> None:
        pass

    async def command(ctx: Ctx) -> None:
        pass

    router.on_text("/demo_remind")(exact)
    router.on_command("/demo_remind")(command)

    assert await router.resolve(_ctx("/demo_remind")) is exact


async def test_command_not_first_word_falls_through_to_fallback():
    router = Router()

    async def command(ctx: Ctx) -> None:
        pass

    async def fb(ctx: Ctx) -> None:
        pass

    router.on_command("/demo_remind")(command)
    router.fallback(fb)

    assert await router.resolve(_ctx("купить /demo_remind")) is fb
