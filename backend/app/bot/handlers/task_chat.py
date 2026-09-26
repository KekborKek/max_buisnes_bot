"""Экран 9 «Своя задача текстом» (docs/screens/09-task-from-chat.md) и вход свободного текста.

Любое сообщение, которое не забрали другие обработчики, приходит сюда (`router.fallback`):
    онбординг не завершён          → `onboarding.use_buttons` + кнопки экрана 1;
    дата и название распознаны     → `confirm` (или `past_date` / `duplicate`);
    похоже на задачу, даты нет     → экран 10, «не нашёл дату»;
    ни даты, ни задачи, пустое название, непонятная кнопка → экран 10, «не понял».

Черновик — `DialogState.data["task_draft"] = {"title", "due_date", "id"}` (`/api/me` читает
title и due_date и сверяет id): его показывает `confirm`, сохраняет «Сохранить», в форму 17
уносит «Изменить» (open_app `task_draft_<id>`, D27, #96). После сохранения или отмены черновик
стирается; кнопка из старого сообщения без черновика — случай «не понял».

Черновик один, а сообщений с кнопками может быть несколько (#87). Каждый новый черновик получает
короткий `id`, и кнопки confirm / duplicate / past_date несут его в payload. Кнопка не текущего
черновика ничего не сохраняет: `task.draft_stale` («Отмена» — просто `cancelled`, текущий черновик
живёт дальше) и событие `task_draft_stale`. Кнопки, отправленные до #87 (payload без id), работают
только с черновиком без id. Payload другого вида («task:saveX», «task:save:») — «не понял».
«Завтра» / «Через неделю» экрана 10 устроены так же: `fb:tomorrow:<id>` (#96).

Payload кнопок (`<id>` — id черновика):
    task:save:<id> — «Сохранить»   task:anyway:<id> — «Добавить всё равно»
    task:yes:<id> — «Да» на past_date   task:cancel:<id> — «Отмена»
    fb:tomorrow:<id> / fb:week:<id> — «Завтра» / «Через неделю» экрана 10
"""

import logging
from datetime import date, datetime, timedelta
from enum import Enum

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


class Press(Enum):
    """Чья кнопка нажата: текущего черновика, прежнего или payload не нашего вида."""

    OWN = "own"
    STALE = "stale"
    BAD = "bad"


def _press(ctx: Ctx, action: str, data: dict) -> Press:
    """Разбор payload кнопки черновика: ровно `action` (кнопка до #87) или «<action>:<id>».

    Кнопка от другого черновика — id в payload не совпадает с id текущего. Черновика нет
    вовсе (сохранён, отменён) — не «устарел»: обработчик ответит «не понял», как раньше.
    Старая кнопка без id совпадает только с черновиком без id (записанным до #87).
    """
    raw_payload = ctx.payload or ""
    if raw_payload == action:
        pressed: str | None = None
    elif raw_payload.startswith(f"{action}:") and len(raw_payload) > len(action) + 1:
        pressed = raw_payload[len(action) + 1 :]
    else:
        return Press.BAD
    raw = data.get(fallback.TASK_DRAFT_KEY)
    if isinstance(raw, dict) and raw.get("id") != pressed:
        return Press.STALE
    return Press.OWN


def _draft_id(data: dict) -> str | None:
    """Id текущего черновика; у черновика до #87 его нет."""
    raw = data.get(fallback.TASK_DRAFT_KEY)
    value = raw.get("id") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


async def _track_stale(ctx: Ctx, action: str) -> None:
    """`action`: save / anyway / yes / cancel (экран 9), tomorrow / week (экран 10)."""
    name = action.removeprefix("task:").removeprefix("fb:")
    await ctx.track("task_draft_stale", {"action": name})


async def _reply_stale(ctx: Ctx, action: str) -> None:
    await _track_stale(ctx, action)
    await ctx.reply(t("task.draft_stale"))


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


async def _load_today(ctx: Ctx) -> tuple[Profile | None, date, str, datetime]:
    """Профиль, сегодня и пояс пользователя и `now` — один на весь обработчик."""
    assert ctx.user_id is not None
    now = common.now()
    profile = await ctx.session.get(Profile, ctx.user_id)
    today, tz = rem.user_today(profile, now)
    return profile, today, tz, now


# --- клавиатуры ----------------------------------------------------------------------------


def payload(action: str, draft_id: str | None) -> str:
    """Payload кнопки черновика: «task:save:<id>» (до 1024 символов — docs/max-api-notes.md).

    Черновик до #87 без id — кнопка тоже без id, как раньше.
    """
    return f"{action}:{draft_id}" if draft_id else action


def confirm_keyboard(draft_id: str) -> dict:
    """«Сохранить» / «Изменить» «Отмена»; «Изменить» — только с MAX_BOT_USERNAME."""
    second = [kb.callback(t("task.btn_cancel"), payload(CANCEL, draft_id))]
    edit = fallback.open_calendar_button("task.btn_edit", fallback.draft_start_param(draft_id))
    if edit is not None:
        second.insert(0, edit)
    return kb.inline_keyboard([kb.callback(t("task.btn_save"), payload(SAVE, draft_id))], second)


def past_date_keyboard(draft_id: str | None) -> dict:
    return kb.inline_keyboard(
        [
            kb.callback(t("task.btn_yes"), payload(YES, draft_id)),
            kb.callback(t("task.btn_cancel"), payload(CANCEL, draft_id)),
        ]
    )


def duplicate_keyboard(draft_id: str | None) -> dict:
    return kb.inline_keyboard(
        [
            kb.callback(t("task.btn_add_anyway"), payload(ANYWAY, draft_id)),
            kb.callback(t("task.btn_cancel"), payload(CANCEL, draft_id)),
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


async def _put_draft(ctx: Ctx, title: str, due_date: date) -> str:
    """Новый черновик с новым id; кнопки прежних сообщений становятся устаревшими."""
    draft_id = fallback.new_draft_id()
    state, data = await ctx.get_state()
    data.pop(fallback.TASK_TITLE_KEY, None)
    data[fallback.TASK_DRAFT_KEY] = fallback.draft(title, due_date, draft_id)
    await ctx.set_state(state, data)
    return draft_id


def will_remind(due_date: date, tz: str, now: datetime) -> bool:
    """Будет ли напоминание, если сохранить сейчас: тот же расчёт, что в `_save` (D11)."""
    planned = rem.plan_task_notification(
        due_date,
        remind_offset_days=REMIND_OFFSET_DAYS,
        remind_hour=REMIND_HOUR,
        tz=tz,
        now=now,
    )
    return planned is not None


async def show_past_date(ctx: Ctx, title: str, due_date: date, today: date) -> None:
    """Год указан явно и дата прошла. «Да» — тот же день ближайшего не прошедшего года."""
    next_date = next_same_day(due_date, today)
    if next_date is None:  # не бывает: 29 февраля встречается раз в 4–8 лет
        await fallback.show_unknown(ctx)
        return
    draft_id = await _put_draft(ctx, title, due_date)
    await ctx.reply(
        t(
            "task.past_date",
            date=format_date(due_date, today),
            next_year_date=format_date(next_date, today),
        ),
        attachments=[past_date_keyboard(draft_id)],
    )


async def propose(
    ctx: Ctx, title: str, due_date: date, today: date, tz: str, now: datetime
) -> None:
    """`confirm` с черновиком; такая же задача на эту дату уже есть — `duplicate`.

    Напоминание обещаем, только если оно будет создано (#87): на сегодня и на завтра после
    10:00 в поясе пользователя его нет — тогда `confirm_no_remind`.
    """
    draft_id = await _put_draft(ctx, title, due_date)
    if await _has_duplicate(ctx, title, due_date):
        await ctx.reply(
            t("task.duplicate", date=format_date(due_date, today)),
            attachments=[duplicate_keyboard(draft_id)],
        )
        return
    day = format_date(due_date, today)
    if will_remind(due_date, tz, now):
        text = t(
            "task.confirm",
            title=title,
            date=day,
            remind_when=t("task.remind_day"),
            hour=REMIND_HOUR,
        )
    else:
        text = t("task.confirm_no_remind", title=title, date=day)
    await ctx.reply(text, attachments=[confirm_keyboard(draft_id)])


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


async def _save(ctx: Ctx, action: str, *, check_duplicate: bool) -> None:
    """«Сохранить» / «Добавить всё равно»: `Task` + `task`-уведомление + `saved`."""
    if ctx.user_id is None:
        return
    profile, today, tz, now = await _load_today(ctx)
    _, data = await ctx.get_state()
    press = _press(ctx, action, data)
    if press is Press.STALE:
        await _reply_stale(ctx, action)
        return
    pending = _draft_from(data) if press is Press.OWN else None
    if profile is None or pending is None:
        await fallback.show_unknown(ctx)
        return
    title, due_date = pending
    if due_date < today:  # черновик пролежал до следующего дня
        await show_past_date(ctx, title, due_date, today)
        return
    if check_duplicate and await _has_duplicate(ctx, title, due_date):
        # Черновик тот же: кнопки исходного confirm продолжают работать.
        await ctx.reply(
            t("task.duplicate", date=format_date(due_date, today)),
            attachments=[duplicate_keyboard(_draft_id(data))],
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
    await rem.sync_task_notification(ctx.session, task, tz=tz, now=now)
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

    profile, today, tz, now = await _load_today(ctx)
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
    await propose(ctx, parsed.title, parsed.due_date, today, tz, now)


@router.on_callback(SAVE)
async def on_save(ctx: Ctx) -> None:
    await _save(ctx, SAVE, check_duplicate=True)


@router.on_callback(ANYWAY)
async def on_add_anyway(ctx: Ctx) -> None:
    await _save(ctx, ANYWAY, check_duplicate=False)


@router.on_callback(YES)
async def on_yes(ctx: Ctx) -> None:
    """«Да» на past_date: дата → тот же день ближайшего не прошедшего года, снова `confirm`."""
    if ctx.user_id is None:
        return
    _, today, tz, now = await _load_today(ctx)
    _, data = await ctx.get_state()
    press = _press(ctx, YES, data)
    if press is Press.STALE:
        await _reply_stale(ctx, YES)
        return
    pending = _draft_from(data) if press is Press.OWN else None
    next_date = next_same_day(pending[1], today) if pending else None
    if pending is None or next_date is None:
        await fallback.show_unknown(ctx)
        return
    await propose(ctx, pending[0], next_date, today, tz, now)


@router.on_callback(CANCEL)
async def on_cancel(ctx: Ctx) -> None:
    """«Отмена». Под устаревшим черновиком — тот же `cancelled`, но текущий черновик живёт."""
    if ctx.user_id is None:
        return
    _, data = await ctx.get_state()
    press = _press(ctx, CANCEL, data)
    if press is Press.BAD:
        await fallback.show_unknown(ctx)
        return
    if press is Press.STALE:
        await _track_stale(ctx, CANCEL)
    else:
        await _forget_draft(ctx)
    await ctx.reply(t("task.cancelled"))


async def _with_date(ctx: Ctx, action: str, days: int) -> None:
    """«Завтра» / «Через неделю» экрана 10: название из `task_title`, дата подставлена.

    Кнопка несёт id черновика «не нашёл дату» (#96): из старого сообщения она не берёт чужое
    название — `draft_stale`, как кнопки экрана 9.
    """
    if ctx.user_id is None:
        return
    _, today, tz, now = await _load_today(ctx)
    _, data = await ctx.get_state()
    press = _press(ctx, action, data)
    if press is Press.STALE:
        await _reply_stale(ctx, action)
        return
    title = data.get(fallback.TASK_TITLE_KEY) if press is Press.OWN else None
    if not isinstance(title, str) or not title:
        await fallback.show_unknown(ctx)
        return
    await propose(ctx, title, today + timedelta(days=days), today, tz, now)


@router.on_callback(fallback.TOMORROW)
async def on_tomorrow(ctx: Ctx) -> None:
    await _with_date(ctx, fallback.TOMORROW, 1)


@router.on_callback(fallback.WEEK)
async def on_week(ctx: Ctx) -> None:
    await _with_date(ctx, fallback.WEEK, 7)
