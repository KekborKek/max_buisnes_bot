"""Демо-команда `/demo_remind <kind>` (docs/spec/reminders.md, «Демо»; T13b, #72).

Открыта всем — решение 28.09: жюри проверяет бота своими аккаунтами MAX и не может ждать настоящих
напоминаний днями, поэтому команда доступна ЛЮБОМУ пользователю, а не только `ADMIN_IDS`. Сразу
присылает пользователю САМОМУ СЕБЕ напоминание указанного вида по его собственному ближайшему
невыполненному обязательству — тем же текстом и кнопками, что шлёт планировщик
(`app/calendar/reminders.py: render_reminder`). Чужие данные команда не читает: `user_id` всегда
берётся из `ctx.user_id`, сообщение всегда уходит через `ctx.reply` (тому же пользователю).
`Notification` не создаём: демо не должно попадать в настоящее расписание отправки. Кнопки
напоминания кодируют `item_type`/`item_id` (`r:<action>:<item_type>:<item_id>`), а не
`notification_id`, поэтому «Отметить выполненным» и «Как сделать» у демо-сообщения работают как у
настоящего — эти обработчики читают событие из БД напрямую
(handlers/done.py, handlers/reminders.py).

`ADMIN_IDS` (`settings.admin_ids`) больше не ограничивает доступ к команде: только помечает вызов
в аналитике (`is_admin`) — пригодится, если понадобится отличить проверки жюри от вызовов команды.

В групповом чате (`ctx.is_group`) команда не выполняется: демо-напоминание несёт личные сроки
вызвавшего, показывать их всем участникам чата нельзя. Ответ — `fallback.show_unknown`, как для
любого непонятого текста (допущение ревью 28.09; альтернатива — молчание, выбрали ответ ради
единообразия с остальными неизвестными командами в группах).

Защита от спама: простой лимит в памяти процесса — не чаще одного вызова в `_RATE_LIMIT_SECONDS`
секунд на `user_id` (см. `_last_used`). В БД не хранится: моделей не меняем (решение 28.09), рестарт
процесса лимит сбрасывает — для демо-команды хакатона этого достаточно.

`/demo_remind digest` — сводка экрана 12 за текущую неделю (#77), см. `_demo_digest`. В подсказку
`demo.usage` вид `digest` не входит: `KINDS` — виды напоминаний по одному обязательству.

`task`-уведомления демо не показывает: схема 30/7/1/overdue к своим задачам не относится
(D11, reminders.md), а демо явно «по ближайшему обязательству».

Аналитика: `demo_remind_used {kind, is_admin}` — на каждый вызов с распознанным видом (включая
`digest` и случай «обязательств нет»); такого события раньше не было в `docs/spec/product.md`,
завели по задаче открытия команды всем (см. отчёт в PR). Клики по кнопкам демо-сообщения трекаются
как обычно существующими обработчиками; `reminder_clicked.kind` для них будет `"unknown"`
(сообщение не отправлялось планировщиком, `Notification` со статусом `sent` не существует) —
известное и принятое допущение, не блокер.
"""

import logging
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.context import Ctx
from app.bot.handlers import common, fallback
from app.bot.router import router
from app.calendar import digest, loader
from app.calendar import reminders as rem
from app.core.config import get_settings
from app.core.models import Profile, UserObligation
from app.core.texts import t

log = logging.getLogger(__name__)

COMMAND = "/demo_remind"
# Только виды по обязательствам: демо явно «по ближайшему обязательству» (reminders.md, «Демо»);
# `task` в схему 30/7/1/overdue не входит (D11) и демо его не показывает.
KINDS: Final[tuple[str, ...]] = ("d30", "d7", "d1", "overdue", "snooze")

_RATE_LIMIT_SECONDS: Final[int] = 10
# user_id -> момент последнего разрешённого вызова. Память процесса (см. docstring выше);
# один бэкенд-процесс на всё приложение (AGENTS.md), поэтому лимит общий и переживает рестарт
# только в пределах текущего запуска.
_last_used: dict[int, datetime] = {}


def _rate_limited(user_id: int) -> bool:
    """True — пользователь уже вызывал команду недавно; иначе засчитывает вызов и пускает дальше."""
    moment = common.now()
    last = _last_used.get(user_id)
    if last is not None and (moment - last).total_seconds() < _RATE_LIMIT_SECONDS:
        return True
    _last_used[user_id] = moment
    return False


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


async def _demo_digest(ctx: Ctx, user_id: int) -> None:
    """`/demo_remind digest` (экран 12, #77): сводка за текущую неделю пн–вс — тем же рендером.

    Как и остальные виды демо, `Notification` и `reminder_sent` не создаёт: настоящая сводка
    в понедельник от показа не пропадает. Событий на неделе нет — `demo.digest_empty`.
    """
    profile = await ctx.session.get(Profile, user_id)
    today, _ = rem.user_today(profile, common.now())
    monday = digest.week_monday(today)
    items = await digest.load_week_items(ctx.session, loader.get_reference(), user_id, monday)
    if not items:
        await ctx.reply(t("demo.digest_empty"))
        return
    text, keyboard = digest.render_digest(items, monday, today)
    await ctx.reply(text, attachments=keyboard)


@router.on_command(COMMAND)
async def demo_remind(ctx: Ctx) -> None:
    if ctx.user_id is None:
        return
    if ctx.is_group:
        # Групповой чат (docs/spec/reminders.md, «Демо», допущение ревью 28.09): демо-напоминание
        # несёт личные сроки вызвавшего — их не показываем всем в чате. Команда как будто не
        # существует, тот же ответ, что и на любой другой непонятый текст.
        await fallback.show_unknown(ctx)
        return
    is_admin = ctx.user_id in get_settings().admin_ids

    kind = _parse_kind(ctx.text)
    if kind != digest.KIND and kind not in KINDS:
        await ctx.reply(t("demo.usage", kinds=", ".join(KINDS)))
        return

    # Лимит — после проверки вида: опечатка в виде не «съедает» следующую попытку.
    if _rate_limited(ctx.user_id):
        # Временный текст техлида (28.09), ждёт UX — content/texts.yaml: demo.rate_limited.
        await ctx.reply(t("demo.rate_limited", seconds=_RATE_LIMIT_SECONDS))
        return

    await ctx.track("demo_remind_used", {"kind": kind, "is_admin": is_admin})

    if kind == digest.KIND:
        await _demo_digest(ctx, ctx.user_id)
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
