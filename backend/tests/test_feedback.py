"""«Написать нам» — обращение в боте (экраны 10, 11; D13; handlers/feedback.py).

Апдейты — фикстуры `message_created` / `callback_start_check` через помощники test_task_chat.
Клиент MAX — FakeMax: пересылка админам видна в `fake_max.sent` с `user_id` админа.
"""

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.bot.handlers import common, feedback
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.models import DialogState, Feedback
from app.core.texts import t
from tests.conftest import FakeMax
from tests.test_task_chat import NOW, USER_ID, buttons, events, onboarded, press, write

pytestmark = pytest.mark.usefixtures("fixture_reference")

ADMINS = [7001, 7002]
AUTHOR = t("feedback.author", name="Борис", user_id=USER_ID)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(common, "now", lambda: NOW)


@pytest.fixture
def admins(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", list(ADMINS))


@pytest.fixture
def no_admins(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", [])


@pytest.fixture
def me_admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", [USER_ID])


async def saved() -> list[Feedback]:
    async with SessionLocal() as s:
        return list((await s.scalars(select(Feedback).order_by(Feedback.id))).all())


async def state() -> str | None:
    async with SessionLocal() as s:
        row = await s.get(DialogState, USER_ID)
        return row.state if row else None


def to_user(fake_max) -> list[dict]:
    return [m for m in fake_max.sent if m["user_id"] == USER_ID]


def to_admins(fake_max) -> list[dict]:
    return [m for m in fake_max.sent if m["user_id"] in ADMINS]


def named(evs: list[tuple[str, dict]], prefix: str = "feedback_") -> list[tuple[str, dict]]:
    return [(name, props) for name, props in evs if name.startswith(prefix)]


# --- полный путь ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["about", "fallback"])
async def test_button_then_text_saves_and_forwards_to_each_admin(fake_max, admins, source):
    await onboarded()
    await process_update(press(f"feedback:start:{source}"), fake_max)

    prompt = fake_max.sent[-1]
    assert prompt["text"] == t("feedback.prompt")
    assert buttons(prompt) == [
        [{"type": "callback", "text": t("feedback.btn_cancel"), "payload": "feedback:cancel"}]
    ]
    assert await state() == feedback.STATE

    await process_update(write("  Не хватает сроков по патенту  "), fake_max)

    [item] = await saved()
    assert (item.user_id, item.text, item.forwarded) == (
        USER_ID,
        "Не хватает сроков по патенту",
        True,
    )
    assert to_user(fake_max)[-1]["text"] == t("feedback.sent")
    forwarded = to_admins(fake_max)
    assert [m["user_id"] for m in forwarded] == ADMINS
    expected = t("feedback.forward", id=item.id, author=AUTHOR, text="Не хватает сроков по патенту")
    assert {m["text"] for m in forwarded} == {expected}
    assert await state() is None
    assert named(await events()) == [
        ("feedback_started", {"source": source}),
        ("feedback_sent", {"length": len("Не хватает сроков по патенту")}),
    ]


async def test_user_reply_goes_before_forward(fake_max, admins):
    """Ответ пользователю уходит первым, пересылка — после (ctx.defer)."""
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    await process_update(write("Спасибо за бота"), fake_max)

    last_three = fake_max.sent[-3:]
    assert [m["user_id"] for m in last_three] == [USER_ID, *ADMINS]


async def test_feedback_command_starts_waiting(fake_max, admins):
    await onboarded()
    await process_update(write("/feedback"), fake_max)

    assert fake_max.sent[-1]["text"] == t("feedback.prompt")
    assert await state() == feedback.STATE
    assert named(await events()) == [("feedback_started", {"source": "command"})]


async def test_feedback_command_with_text_saves_at_once(fake_max, admins):
    await onboarded()
    await process_update(write("/feedback Добавьте ЕНС"), fake_max)

    [item] = await saved()
    assert item.text == "Добавьте ЕНС"
    assert to_user(fake_max)[-1]["text"] == t("feedback.sent")
    assert len(to_admins(fake_max)) == len(ADMINS)
    assert await state() is None


async def test_without_admins_only_saved_and_warned(fake_max, no_admins, caplog):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    await process_update(write("Текст"), fake_max)

    [item] = await saved()
    assert item.forwarded is False
    assert [m["text"] for m in fake_max.sent] == [t("feedback.prompt"), t("feedback.sent")]
    assert "ADMIN_IDS пуст" in caplog.text


async def test_works_for_new_user_without_profile(fake_max, admins):
    """Обращение до /start: пользователь создаётся, внешний ключ не падает."""
    await process_update(write("/feedback Не могу начать"), fake_max)

    [item] = await saved()
    assert item.user_id == USER_ID


# --- отмена, пустое, длинное ----------------------------------------------------------------


async def test_cancel_exits_without_saving(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    await process_update(press("feedback:cancel"), fake_max)

    assert fake_max.sent[-1]["text"] == t("feedback.cancelled")
    assert await saved() == []
    assert await state() is None
    assert to_admins(fake_max) == []
    assert ("feedback_cancelled", {}) in named(await events())

    # после отмены обычный текст снова идёт в сценарий задач, а не в обращение
    await process_update(write("привет"), fake_max)
    assert await saved() == []


async def test_non_text_message_asks_again(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    photo = write("x")
    photo["message"]["body"] = {"mid": "mid-feedback-photo", "attachments": [{"type": "image"}]}
    await process_update(photo, fake_max)

    assert fake_max.sent[-1]["text"] == t("feedback.empty")
    assert await state() == feedback.STATE
    assert await saved() == []


async def test_whitespace_asks_again(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    await process_update(write("   "), fake_max)

    assert fake_max.sent[-1]["text"] == t("feedback.empty")
    assert await state() == feedback.STATE


async def test_too_long_asks_to_shorten_then_accepts(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    long = "а" * (feedback.MAX_LENGTH + 1)
    await process_update(write(long), fake_max)

    assert fake_max.sent[-1]["text"] == t(
        "feedback.too_long", limit=feedback.MAX_LENGTH, length=len(long)
    )
    assert await saved() == []
    assert await state() == feedback.STATE

    await process_update(write("а" * feedback.MAX_LENGTH), fake_max)
    [item] = await saved()
    assert len(item.text) == feedback.MAX_LENGTH


# --- другое действие посреди ожидания --------------------------------------------------------


async def test_start_command_while_waiting_works_and_resets(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    await process_update(write("/start"), fake_max)

    assert fake_max.sent[-1]["text"] != t("feedback.sent")
    assert await saved() == []
    assert await state() is None


async def test_about_command_while_waiting_works_and_resets(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:fallback"), fake_max)
    await process_update(write("/about"), fake_max)

    assert fake_max.sent[-1]["text"].startswith(t("about.body", last_checked="01.01.2026"))
    assert await saved() == []
    assert await state() is None

    await process_update(write("привет"), fake_max)  # уже не обращение
    assert await saved() == []


async def test_other_button_while_waiting_resets(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    await process_update(press("about:open"), fake_max)

    assert await state() is None


# --- ошибка пересылки ----------------------------------------------------------------------


class FailingAdminMax(FakeMax):
    """Сообщения админам падают, пользователю — уходят."""

    def __init__(self, failing: set[int]) -> None:
        super().__init__()
        self.failing = failing

    async def send_message(self, text, *, user_id=None, chat_id=None, attachments=None, fmt=None):
        if user_id in self.failing:
            raise RuntimeError("MAX недоступен")
        return await super().send_message(
            text, user_id=user_id, chat_id=chat_id, attachments=attachments, fmt=fmt
        )


async def test_forward_failure_does_not_break_reply(admins, caplog):
    fake = FailingAdminMax(set(ADMINS))
    await onboarded()
    await process_update(press("feedback:start:about"), fake)
    await process_update(write("Текст обращения"), fake)

    assert fake.sent[-1] == {
        "text": t("feedback.sent"),
        "user_id": USER_ID,
        "chat_id": None,
        "attachments": None,
    }
    [item] = await saved()
    assert item.forwarded is False
    assert "не удалось переслать обращение" in caplog.text
    assert ("reply_send_failed", {"update_type": "message_created", "count": 1}) not in (
        await events()
    )


async def test_forward_partial_failure_marks_forwarded(admins):
    fake = FailingAdminMax({ADMINS[0]})
    await onboarded()
    await process_update(write("/feedback Текст"), fake)

    [item] = await saved()
    assert item.forwarded is True
    assert [m["user_id"] for m in fake.sent if m["user_id"] in ADMINS] == [ADMINS[1]]


# --- идемпотентность -----------------------------------------------------------------------


async def test_repeated_update_does_not_create_second_feedback(fake_max, admins):
    await onboarded()
    await process_update(press("feedback:start:about"), fake_max)
    update = write("Одно обращение")
    await process_update(update, fake_max)
    await process_update(update, fake_max)  # MAX доставил повторно

    assert len(await saved()) == 1
    assert len(to_admins(fake_max)) == len(ADMINS)


async def test_repeated_update_via_command_does_not_duplicate(fake_max, admins):
    await onboarded()
    update = write("/feedback Одно обращение")
    await process_update(update, fake_max)
    await process_update(update, fake_max)

    assert len(await saved()) == 1


# --- /feedback_list ------------------------------------------------------------------------


async def test_feedback_list_for_admin(fake_max, me_admin):
    await onboarded()
    await process_update(write("/feedback Первое"), fake_max)
    await process_update(write("/feedback " + "б" * 150), fake_max)
    await process_update(write("/feedback_list"), fake_max)

    text = fake_max.sent[-1]["text"]
    lines = text.split("\n")
    assert lines[0] == t("feedback.list_title", count=2)
    first, second = await saved()
    # свежие сверху; время — по Москве (NOW = 12:00 МСК, created_at пишет база)
    assert lines[1].startswith(f"#{second.id} · ")
    assert lines[1].endswith(f": {'б' * feedback.LIST_PREVIEW}…")
    assert lines[2].startswith(f"#{first.id} · ")
    assert lines[2].endswith(f"{AUTHOR}: Первое")
    assert t("feedback.list_not_forwarded") not in text  # админ = автор, пересылка прошла


async def test_feedback_list_marks_not_forwarded(fake_max, me_admin):
    fake = FailingAdminMax({USER_ID})
    await onboarded()
    await process_update(write("/feedback Первое"), fake)
    # ответ пользователю тоже падает (он же админ) — обращение всё равно сохранено
    [item] = await saved()
    assert item.forwarded is False

    await process_update(write("/feedback_list"), fake_max)
    assert (
        fake_max.sent[-1]["text"]
        .split("\n")[1]
        .endswith(f"{AUTHOR}{t('feedback.list_not_forwarded')}: Первое")
    )


async def test_feedback_list_limited_to_ten(fake_max, me_admin):
    await onboarded()
    for n in range(12):
        await process_update(write(f"/feedback Обращение {n}"), fake_max)
    await process_update(write("/feedback_list"), fake_max)

    lines = fake_max.sent[-1]["text"].split("\n")
    assert lines[0] == t("feedback.list_title", count=feedback.LIST_LIMIT)
    assert len(lines) == feedback.LIST_LIMIT + 1
    assert lines[1].endswith("Обращение 11")


async def test_feedback_list_empty(fake_max, me_admin):
    await onboarded()
    await process_update(write("/feedback_list"), fake_max)

    assert fake_max.sent[-1]["text"] == t("feedback.list_empty")


async def test_feedback_list_for_non_admin_is_unknown(fake_max, admins):
    await onboarded()
    await process_update(write("/feedback Секрет"), fake_max)
    await process_update(write("/feedback_list"), fake_max)

    assert fake_max.sent[-1]["text"] == t("fallback.unknown")
    assert "Секрет" not in fake_max.sent[-1]["text"]
