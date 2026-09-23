"""Экран 6: кнопка «Напомнить завтра» (`r:snooze:<item_type>:<item_id>`).

Остальные кнопки напоминания — «Отметить выполненным» и «Как сделать» — обрабатывает T7
(экраны 7, 8). Сами напоминания отправляет планировщик: app/calendar/reminders.py.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.bot.context import Ctx
from app.bot.handlers import common
from app.bot.router import router
from app.calendar import loader
from app.calendar import reminders as rem
from app.core.models import Notification, Profile
from app.core.texts import t

log = logging.getLogger(__name__)

SNOOZE_PREFIX = "r:snooze:"
# Кнопка «Напомнить завтра» есть только в сообщении d7 (таблица экрана 6).
_SOURCE_KIND = "d7"


def parse_payload(payload: str | None) -> tuple[str, int] | None:
    """`r:<action>:<item_type>:<item_id>` → (item_type, item_id); чужой формат — None."""
    parts = (payload or "").split(":")
    if len(parts) != 4 or parts[2] not in ("obligation", "task") or not parts[3].isdigit():
        return None
    return parts[2], int(parts[3])


@router.on_callback(SNOOZE_PREFIX)
async def snooze(ctx: Ctx) -> None:
    """Создаёт `snooze` на завтра и убирает кнопку из исходного сообщения (правило 3).

    Порядок — ради SQLite: сначала только чтение (в WAL блокировку записи не берёт), потом
    правка сообщения в MAX (сеть — до первой записи, правило диспетчера), потом запись.
    Решение «создавать ли snooze» принимается после первой записи (`ctx.track`), то есть под
    блокировкой записи: два быстрых нажатия не создадут два `snooze`.
    """
    parsed = parse_payload(ctx.payload)
    if parsed is None or ctx.user_id is None:
        log.warning("snooze: непонятный payload %r", ctx.payload)
        return
    item_type, item_id = parsed
    now = common.now()

    # 1. Чтение: событие и профиль — для текста сообщения и времени напоминания.
    profile = await ctx.session.get(Profile, ctx.user_id)
    today, tz = rem.user_today(profile, now)
    hour = int(rem.reminder_settings(profile.reminders if profile else None)["hour"])
    item = await rem.load_reminder_item(
        ctx.session,
        loader.get_reference(),
        notification_id=0,
        user_id=ctx.user_id,
        item_type=item_type,
        item_id=item_id,
    )

    # Событие отмечено, удалено или чужое — напоминать не о чем, snooze не создаём.
    # Ответ «уже отмечено» — экран 8 (T7); пока просто не обещаем напомнить.
    if item is None:
        log.info("snooze: событие %s:%s неактуально", item_type, item_id)
        await ctx.track("reminder_clicked", {"kind": _SOURCE_KIND, "action": "snooze"})
        return

    # 2. Сеть: кнопка исчезает из исходного сообщения при любом исходе (правило 3).
    await _remove_snooze_button(ctx, item, today)

    # 3. Запись. Первая запись (track) берёт блокировку — проверка ниже видит свежие данные.
    await ctx.track("reminder_clicked", {"kind": _SOURCE_KIND, "action": "snooze"})
    tomorrow = today + timedelta(days=1)
    # Завтра уже d1 или день срока — d1 придёт и так: snooze не создаём, ответ тот же.
    if tomorrow < item.due_date - timedelta(days=1) and not await _has_snooze(
        ctx, item_type, item_id
    ):
        ctx.session.add(
            Notification(
                user_id=ctx.user_id,
                item_type=item_type,
                item_id=item_id,
                kind="snooze",
                send_at=rem.send_at_utc(tomorrow, hour, tz),
                status="pending",
                attempts=0,
            )
        )
    await ctx.reply(t("reminder.snooze_ok", hour=hour))


async def _remove_snooze_button(ctx: Ctx, item: rem.ReminderItem, today: date) -> None:
    """Перерисовывает сообщение d7 без «Напомнить завтра» тем же рендером, что и отправка."""
    mid = (((ctx.update.get("message") or {}).get("body")) or {}).get("mid")
    if not mid:
        log.warning("snooze: в апдейте нет message.body.mid — кнопку не убираем")
        return
    text, _ = rem.render_reminder(_SOURCE_KIND, [item], today)
    keyboard = rem.reminder_keyboard(_SOURCE_KIND, item.item_type, item.item_id, with_snooze=False)
    try:
        await ctx.max.edit_message(mid, text, attachments=keyboard or [], fmt=None)
    except Exception:
        log.exception("snooze: не удалось убрать кнопку из сообщения %s", mid)


async def _has_snooze(ctx: Ctx, item_type: str, item_id: int) -> bool:
    """Правило 3: `snooze` по событию бывает один раз — в любом статусе."""
    found = await ctx.session.scalar(
        select(Notification.id)
        .where(
            Notification.item_type == item_type,
            Notification.item_id == item_id,
            Notification.kind == "snooze",
        )
        .limit(1)
    )
    return found is not None
