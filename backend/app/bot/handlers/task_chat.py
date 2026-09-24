"""Экран 9 «Своя задача текстом» (docs/screens/09-task-from-chat.md) и вход свободного текста.

Любое сообщение, которое не забрали другие обработчики, приходит сюда (`router.fallback`):
    онбординг не завершён          → `onboarding.use_buttons` + кнопки экрана 1;
    дата и название распознаны     → `confirm` (или `past_date` / `duplicate`);
    похоже на задачу, даты нет     → экран 10, «не нашёл дату»;
    ни даты, ни задачи, пустое название, непонятная кнопка → экран 10, «не понял».

Черновик — `DialogState.data["task_draft"] = {"title", "due_date"}` (формат читает `/api/me`):
его показывает `confirm`, сохраняет «Сохранить», в форму 17 уносит «Изменить» (open_app, D27).
После сохранения или отмены черновик стирается; кнопка из старого сообщения без черновика —
случай «не понял».

Payload кнопок:
    task:save — «Сохранить»   task:anyway — «Добавить всё равно»   task:yes — «Да» на past_date
    task:cancel — «Отмена»    fb:tomorrow / fb:week — «Завтра» / «Через неделю» экрана 10
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.bot import keyboards as kb
from app.bot.context import Ctx
from app.bot.dateparse import ParseStatus, parse_task_from_text
from app.bot.formatting import format_date, plural
from app.bot.handlers import common, fallback
from app.bot.handlers.start import start_keyboard
from app.bot.router import router
from app.calendar import reminders as rem
from app.core.models import Profile, Task, UserObligation
from app.core.texts import t

log = logging.getLogger(__name__)

SAVE = "task:save"
ANYWAY = "task:anyway"
YES = "task:yes"
CANCEL = "task:cancel"

# Данные экрана 9: одно напоминание за день в 10:00 (D11). Не налоговые данные.
REMIND_OFFSET_DAYS = 1
REMIND_HOUR = 10
# «Да» на past_date: ближайший не прошедший год с тем же днём и месяцем (29 февраля — до 8 лет).
_YEARS_AHEAD = 8


def is_onboarded(profile: Profile | None) -> bool:
    """Онбординг пройден: есть ответы на вопросы 1–3 (пояс по умолчанию есть всегда)."""
    return (
        profile is not None
        and profile.income_band is not None
        and profile.regime is not None
        and profile.has_employees is not None
    )


def next_same_day(day: date, today: date) -> date | None:
    """Тот же день и месяц, не раньше сегодня: «Сохранить на {next_year_date}?»."""
    for year in range(today.year, today.year + _YEARS_AHEAD + 1):
        try:
            candidate = date(year, day.month, day.day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def _draft_from(data: dict) -> tuple[str, date] | None:
    """Черновик, ждущий подтверждения. Пока висит «не нашёл дату» (`task_title`) — его нет."""
    raw = data.get(fallback.TASK_DRAFT_KEY)
    if fallback.TASK_TITLE_KEY in data or not isinstance(raw, dict):
        return None
    title, due = raw.get("title"), raw.get("due_date")
    if not isinstance(title, str) or not title or not isinstance(due, str):
        return None
    try:
        return title, date.fromisoformat(due)
    except ValueError:
        log.warning("task_draft с неразборчивой датой %r", due)
        return None


async def _forget_draft(ctx: Ctx) -> None:
    state, data = await ctx.get_state()
    data.pop(fallback.TASK_DRAFT_KEY, None)
    data.pop(fallback.TASK_TITLE_KEY, None)
    await ctx.set_state(state, data)


async def _load_today(ctx: Ctx) -> tuple[Profile | None, date, str]:
    assert ctx.user_id is not None
    profile = await ctx.session.get(Profile, ctx.user_id)
    today, tz = rem.user_today(profile, common.now())
    return profile, today, tz


# --- клавиатуры ----------------------------------------------------------------------------


def confirm_keyboard() -> dict:
    """«Сохранить» / «Изменить» «Отмена»; «Изменить» — только с MAX_BOT_USERNAME."""
    second = [kb.callback(t("task.btn_cancel"), CANCEL)]
    edit = fallback.open_calendar_button("task.btn_edit", fallback.DRAFT_START_PARAM)
    if edit is not None:
        second.insert(0, edit)
    return kb.inline_keyboard([kb.callback(t("task.btn_save"), SAVE)], second)


def past_date_keyboard() -> dict:
    return kb.inline_keyboard(
        [kb.callback(t("task.btn_yes"), YES), kb.callback(t("task.btn_cancel"), CANCEL)]
    )


def duplicate_keyboard() -> dict:
    return kb.inline_keyboard(
        [
            kb.callback(t("task.btn_add_anyway"), ANYWAY),
            kb.callback(t("task.btn_cancel"), CANCEL),
        ]
    )


def saved_attachments() -> list[dict] | None:
    button = fallback.open_calendar_button("task.btn_open")
    return [kb.inline_keyboard([button])] if button is not None else None


# --- экраны --------------------------------------------------------------------------------


async def _has_duplicate(ctx: Ctx, title: str, due_date: date) -> bool:
    found = await ctx.session.scalar(
        select(Task.id)
        .where(
            Task.user_id == ctx.user_id,
            Task.title == title,
            Task.due_date == due_date,
            Task.deleted_at.is_(None),
        )
        .limit(1)
    )
    return found is not None


async def _put_draft(ctx: Ctx, title: str, due_date: date) -> None:
    state, data = await ctx.get_state()
    data.pop(fallback.TASK_TITLE_KEY, None)
    data[fallback.TASK_DRAFT_KEY] = fallback.draft(title, due_date)
    await ctx.set_state(state, data)


async def show_past_date(ctx: Ctx, title: str, due_date: date, today: date) -> None:
    """Год указан явно и дата прошла. «Да» — тот же день ближайшего не прошедшего года."""
    next_date = next_same_day(due_date, today)
    if next_date is None:  # не бывает: 29 февраля встречается раз в 4–8 лет
        await fallback.show_unknown(ctx)
        return
    await _put_draft(ctx, title, due_date)
    await ctx.reply(
        t(
            "task.past_date",
            date=format_date(due_date, today),
            next_year_date=format_date(next_date, today),
        ),
        attachments=[past_date_keyboard()],
    )


async def propose(ctx: Ctx, title: str, due_date: date, today: date) -> None:
    """`confirm` с черновиком; такая же задача на эту дату уже есть — `duplicate`."""
    await _put_draft(ctx, title, due_date)
    if await _has_duplicate(ctx, title, due_date):
        await ctx.reply(
            t("task.duplicate", date=format_date(due_date, today)),
            attachments=[duplicate_keyboard()],
        )
        return
    await ctx.reply(
        t(
            "task.confirm",
            title=title,
            date=format_date(due_date, today),
            remind_when=t("task.remind_day"),
            hour=REMIND_HOUR,
        ),
        attachments=[confirm_keyboard()],
    )


async def _calendar_count(ctx: Ctx, today: date) -> int:
    """Невыполненные события с датой от сегодня: обязательства и неудалённые задачи."""
    obligations = await ctx.session.scalar(
        select(func.count())
        .select_from(UserObligation)
        .where(
            UserObligation.user_id == ctx.user_id,
            UserObligation.done_at.is_(None),
            UserObligation.due_date >= today,
        )
    )
    tasks = await ctx.session.scalar(
        select(func.count())
        .select_from(Task)
        .where(
            Task.user_id == ctx.user_id,
            Task.done_at.is_(None),
            Task.deleted_at.is_(None),
            Task.due_date >= today,
        )
    )
    return int(obligations or 0) + int(tasks or 0)


def count_word(count: int) -> str:
    return plural(
        count,
        t("calendar_ready.count_one"),
        t("calendar_ready.count_few"),
        t("calendar_ready.count_many"),
    )


async def _save(ctx: Ctx, *, check_duplicate: bool) -> None:
    """«Сохранить» / «Добавить всё равно»: `Task` + `task`-уведомление + `saved`."""
    if ctx.user_id is None:
        return
    profile, today, tz = await _load_today(ctx)
    _, data = await ctx.get_state()
    pending = _draft_from(data)
    if profile is None or pending is None:
        await fallback.show_unknown(ctx)
        return
    title, due_date = pending
    if due_date < today:  # черновик пролежал до следующего дня
        await show_past_date(ctx, title, due_date, today)
        return
    if check_duplicate and await _has_duplicate(ctx, title, due_date):
        await ctx.reply(
            t("task.duplicate", date=format_date(due_date, today)),
            attachments=[duplicate_keyboard()],
        )
        return

    task = Task(
        user_id=ctx.user_id,
        title=title,
        due_date=due_date,
        remind_offset_days=REMIND_OFFSET_DAYS,
        remind_hour=REMIND_HOUR,
    )
    ctx.session.add(task)
    await ctx.session.flush()
    await rem.sync_task_notification(ctx.session, task, tz=tz, now=common.now())
    await ctx.track("task_created", {"source": "chat", "remind_offset": REMIND_OFFSET_DAYS})
    await _forget_draft(ctx)
    count = await _calendar_count(ctx, today)
    await ctx.reply(
        t("task.saved", count=count, count_word=count_word(count)),
        attachments=saved_attachments(),
    )


# --- обработчики ---------------------------------------------------------------------------


@router.fallback
async def on_free_text(ctx: Ctx) -> None:
    """Всё, что не забрали другие обработчики: текст — в разбор, остальное — «не понял»."""
    if ctx.update_type not in ("message_created", "message_callback"):
        return  # служебные события со своими обработчиками — в handlers/service.py
    if ctx.user_id is None:
        return
    if ctx.update_type == "message_callback" or not (ctx.text or "").strip():
        await fallback.show_unknown(ctx)
        return

    profile, today, _ = await _load_today(ctx)
    if not is_onboarded(profile):
        # До конца онбординга свободный текст — забота экрана 2 (use_buttons).
        await ctx.reply(t("onboarding.use_buttons"), attachments=[start_keyboard()])
        return

    parsed = parse_task_from_text(ctx.text or "", today)
    if parsed.status is ParseStatus.UNKNOWN:
        await fallback.show_unknown(ctx)
        return
    if parsed.status is ParseStatus.NO_DATE:
        await fallback.show_no_date(ctx, parsed.title or "", today)
        return
    assert parsed.due_date is not None
    if not parsed.title:
        # В сообщении только дата. Текста в спеке нет — task.empty_title: TODO (строка в PR).
        await fallback.show_unknown(ctx, text_key="task.empty_title")
        return
    if parsed.explicit_year and parsed.due_date < today:
        await show_past_date(ctx, parsed.title, parsed.due_date, today)
        return
    await propose(ctx, parsed.title, parsed.due_date, today)


@router.on_callback(SAVE)
async def on_save(ctx: Ctx) -> None:
    await _save(ctx, check_duplicate=True)


@router.on_callback(ANYWAY)
async def on_add_anyway(ctx: Ctx) -> None:
    await _save(ctx, check_duplicate=False)


@router.on_callback(YES)
async def on_yes(ctx: Ctx) -> None:
    """«Да» на past_date: дата → тот же день ближайшего не прошедшего года, снова `confirm`."""
    if ctx.user_id is None:
        return
    _, today, _ = await _load_today(ctx)
    _, data = await ctx.get_state()
    pending = _draft_from(data)
    next_date = next_same_day(pending[1], today) if pending else None
    if pending is None or next_date is None:
        await fallback.show_unknown(ctx)
        return
    await propose(ctx, pending[0], next_date, today)


@router.on_callback(CANCEL)
async def on_cancel(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    await _forget_draft(ctx)
    await ctx.reply(t("task.cancelled"))


async def _with_date(ctx: Ctx, days: int) -> None:
    """«Завтра» / «Через неделю» экрана 10: название из `task_title`, дата подставлена."""
    if ctx.user_id is None:
        return
    _, today, _ = await _load_today(ctx)
    _, data = await ctx.get_state()
    title = data.get(fallback.TASK_TITLE_KEY)
    if not isinstance(title, str) or not title:
        await fallback.show_unknown(ctx)
        return
    await propose(ctx, title, today + timedelta(days=days), today)


@router.on_callback(fallback.TOMORROW)
async def on_tomorrow(ctx: Ctx) -> None:
    await _with_date(ctx, 1)


@router.on_callback(fallback.WEEK)
async def on_week(ctx: Ctx) -> None:
    await _with_date(ctx, 7)
