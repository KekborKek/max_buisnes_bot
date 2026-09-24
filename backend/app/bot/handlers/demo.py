"""Демо-команда `/demo_remind <kind>` (docs/spec/reminders.md, «Демо»; T13b, #72).

Только для `user_id` из `ADMIN_IDS` (`settings.admin_ids`): сразу присылает себе напоминание
указанного вида по ближайшему невыполненному обязательству — тем же текстом и кнопками, что
шлёт планировщик (`app/calendar/reminders.py: render_reminder`). `Notification` не создаём:
демо не должно попадать в настоящее расписание отправки. Кнопки напоминания кодируют
`item_type`/`item_id` (`r:<action>:<item_type>:<item_id>`), а не `notification_id`, поэтому
«Отметить выполненным» и «Как сделать» у демо-сообщения работают как у настоящего — эти
обработчики читают событие из БД напрямую (handlers/done.py, handlers/reminders.py).

Для не-админа команда как будто не существует: ответ ровно `fallback.show_unknown`, неотличимый
от любого другого непонятого текста.

`task`-уведомления демо не показывает: схема 30/7/1/overdue к своим задачам не относится
(D11, reminders.md), а демо явно «по ближайшему обязательству».

Аналитика: событие на сам показ демо-напоминания не заводим — такого события нет в таблице
`docs/spec/product.md` (см. отчёт в PR). Клики по кнопкам демо-сообщения трекаются как обычно
существующими обработчиками; `reminder_clicked.kind` для них будет `"unknown"` (сообщение не
отправлялось планировщиком, `Notification` со статусом `sent` не существует) — известное и
принятое допущение, не блокер.
"""

import logging
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.context import Ctx
from app.bot.handlers import common, fallback
from app.bot.router import router
from app.calendar import loader
from app.calendar import reminders as rem
from app.core.config import get_settings
from app.core.models import Profile, UserObligation
from app.core.texts import t

log = logging.getLogger(__name__)

COMMAND = "/demo_remind"
# Только виды по обязательствам: демо явно «по ближайшему обязательству» (reminders.md, «Демо»);
# `task` в схему 30/7/1/overdue не входит (D11) и демо его не показывает.
KINDS: Final[tuple[str, ...]] = ("d30", "d7", "d1", "overdue", "snooze")


def _parse_kind(text: str | None) -> str:
    """Второе слово команды в нижнем регистре; аргумента нет — пустая строка."""
    parts = (text or "").strip().split(maxsplit=1)
    return parts[1].strip().lower() if len(parts) > 1 else ""


async def _nearest_obligation_id(
    session: AsyncSession, user_id: int, catalog_ids: set[str]
) -> int | None:
    """id ближайшего невыполненного `UserObligation`, у которого есть запись в справочнике.

    Есть в БД, но пропало из справочника (обновили obligations.yaml) — пропускаем.
    """
    rows = await session.scalars(
        select(UserObligation)
        .where(UserObligation.user_id == user_id, UserObligation.done_at.is_(None))
        .order_by(UserObligation.due_date, UserObligation.id)
    )
    for uo in rows:
        if uo.obligation_id in catalog_ids:
            return uo.id
    return None


@router.on_command(COMMAND)
async def demo_remind(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    if ctx.user_id not in get_settings().admin_ids:
        # Для обычного пользователя команды как будто не существует (reminders.md, «Демо»).
        await fallback.show_unknown(ctx)
        return

    kind = _parse_kind(ctx.text)
    if kind not in KINDS:
        await ctx.reply(t("demo.usage", kinds=", ".join(KINDS)))
        return

    reference = loader.get_reference()
    catalog_ids = {ob.id for ob in reference.catalog.obligations}
    item_id = await _nearest_obligation_id(ctx.session, ctx.user_id, catalog_ids)
    if item_id is None:
        await ctx.reply(t("demo.no_items"))
        return

    item = await rem.load_reminder_item(
        ctx.session,
        reference,
        notification_id=0,
        user_id=ctx.user_id,
        item_type="obligation",
        item_id=item_id,
    )
    if item is None:
        # Успело отмениться между чтением и этой строкой (гонка) — считаем, что показать нечего.
        await ctx.reply(t("demo.no_items"))
        return

    profile = await ctx.session.get(Profile, ctx.user_id)
    today, _ = rem.user_today(profile, common.now())
    text, keyboard = rem.render_reminder(kind, [item], today)
    await ctx.reply(text, attachments=keyboard)
