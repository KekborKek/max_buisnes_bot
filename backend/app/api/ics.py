"""Лента iCalendar: «Добавить в календарь телефона» (экран 19).

`GET /api/ics/link` (с initData) отдаёт ссылку на ленту, `GET /api/ics/{token}.ics` — саму
ленту без initData: её открывает браузер или календарь телефона, заголовка у них нет.

Доступ — по токену в ссылке: `<user_id>-<подпись>`, подпись — HMAC-SHA256 от user_id на ключе,
выведенном из MAX_WEBHOOK_SECRET с меткой домена `ics-v1`. Токен stateless: в БД ничего не
хранится, новая переменная окружения не нужна. Смена MAX_WEBHOOK_SECRET отзывает все ссылки.
Неверный токен — 404, как у несуществующего адреса: не подсказываем, что формат угадан.

Путь — только под `/api/`: остальные пути прокси отдаёт мини-аппу (HTML с кодом 200).
"""

import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime, time
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import service
from app.api.deps import current_time, current_user_id, optional_reference
from app.api.ics_format import FeedEvent, render_calendar
from app.api.schemas import IcsLinkResponse
from app.calendar.reminders import reminder_settings
from app.calendar.types import Reference
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.events import track
from app.core.models import Event, Task, UserObligation

log = logging.getLogger(__name__)

router = APIRouter()

UserId = Annotated[int, Depends(current_user_id)]
Session = Annotated[AsyncSession, Depends(get_session)]
Now = Annotated[datetime, Depends(current_time)]
OptionalRef = Annotated[Reference | None, Depends(optional_reference)]

KEY_LABEL = b"ics-v1"  # метка домена: тот же секрет в другом месте даёт другой ключ
SIG_BYTES = 16  # 128 бит подписи — перебор бессмыслен
_TOKEN = re.compile(r"(\d{1,20})-([0-9a-f]{32})")

# Название календаря у пользователя. Временный текст техлида (28.09), ждёт UX.
CALENDAR_NAME = "Календарь ИП"
FILE_NAME = "calendar-ip.ics"
FEED_FETCHED = "ics_feed_fetched"


def current_settings() -> Settings:
    """Настройки процесса. Отдельная зависимость — тесты подменяют секрет и адрес."""
    return get_settings()


Conf = Annotated[Settings, Depends(current_settings)]


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode(), KEY_LABEL, hashlib.sha256).digest()


def _signature(user_id: int, secret: str) -> str:
    mac = hmac.new(_key(secret), str(user_id).encode(), hashlib.sha256)
    return mac.digest()[:SIG_BYTES].hex()


def make_token(user_id: int, secret: str) -> str:
    return f"{user_id}-{_signature(user_id, secret)}"


def user_from_token(token: str, secret: str) -> int | None:
    """user_id из верного токена; неверный, чужой по подписи или пустой секрет — None."""
    match = _TOKEN.fullmatch(token)
    if not secret or match is None:
        return None
    user_id = int(match.group(1))
    if not hmac.compare_digest(match.group(2), _signature(user_id, secret)):
        return None
    return user_id


def _base_url(settings: Settings, request: Request) -> str:
    """PUBLIC_BASE_URL; пуст (локальная разработка) — адрес, по которому пришёл запрос."""
    return (settings.public_base_url or str(request.base_url)).rstrip("/")


def _uid_domain(settings: Settings) -> str:
    """Домен в UID: от PUBLIC_BASE_URL, чтобы не зависеть от адреса запроса (UID стабилен)."""
    return urlsplit(settings.public_base_url).hostname or "localhost"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


@router.get(
    "/ics/link",
    response_model=IcsLinkResponse,
    summary="Ссылка на ленту iCalendar пользователя (экран 19)",
    responses={503: {"description": "Секрет для подписи ссылок не настроен"}},
)
async def ics_link(user_id: UserId, request: Request, settings: Conf) -> IcsLinkResponse:
    secret = settings.max_webhook_secret
    if not secret:
        # В проде секрет обязателен (config.py), здесь — только незаполненный локальный .env.
        log.warning("MAX_WEBHOOK_SECRET пуст: ссылку на ленту iCalendar подписать нечем")
        raise HTTPException(status_code=503, detail="ics_unavailable")
    url = f"{_base_url(settings, request)}/api/ics/{make_token(user_id, secret)}.ics"
    webcal = "webcal://" + url.split("://", 1)[1]
    return IcsLinkResponse(url=url, webcal_url=webcal)


async def _feed_events(
    session: AsyncSession, user_id: int, reference: Reference, domain: str
) -> list[FeedEvent]:
    """Невыполненные обязательства и свои задачи (кроме удалённых), по дате."""
    profile = await service.load_profile(session, user_id)
    hour = int(reminder_settings(profile.reminders if profile else None)["hour"])
    by_id = service.catalog_index(reference)

    events: list[FeedEvent] = []
    uos = await session.scalars(
        select(UserObligation).where(
            UserObligation.user_id == user_id, UserObligation.done_at.is_(None)
        )
    )
    for uo in uos:
        ob = by_id.get(uo.obligation_id)
        if ob is None:  # пропало из справочника — как в календаре мини-аппа, не показываем
            continue
        events.append(
            FeedEvent(
                uid=f"obligation-{uo.id}@{domain}",
                day=uo.due_date,
                summary=ob.title,
                description=f"{ob.norm}\n{ob.source_url}",
                url=ob.source_url,
                # Накануне в час напоминаний пользователя (экран 13), как d1 в боте
                alarm_before_min=24 * 60 - hour * 60,
            )
        )
    tasks = await session.scalars(
        select(Task).where(
            Task.user_id == user_id, Task.deleted_at.is_(None), Task.done_at.is_(None)
        )
    )
    for task in tasks:
        minute = task.remind_minute or 0
        events.append(
            FeedEvent(
                uid=f"task-{task.id}@{domain}",
                day=task.due_date,
                summary=task.title,
                # Когда сам пользователь попросил напомнить (экран 17): за N дней в ЧЧ:ММ
                alarm_before_min=task.remind_offset_days * 24 * 60
                - (task.remind_hour * 60 + minute),
            )
        )
    events.sort(key=lambda e: (e.day, e.uid))
    return events


async def _track_fetch_daily(session: AsyncSession, user_id: int, items: int) -> None:
    """ics_feed_fetched — не чаще раза в сутки (UTC) на пользователя.

    Календарь-подписчик опрашивает ленту сам, часто и без участия человека; событие на каждый
    запрос мерило бы настройки клиентов и раздувало таблицу. Раз в сутки — ответ на вопрос
    «сколько людей пользуются лентой». Часы — настоящие: created_at события пишется по ним.
    """
    day_start = datetime.combine(datetime.now(UTC).date(), time(), tzinfo=UTC)
    seen = await session.scalar(
        select(Event.id)
        .where(
            Event.user_id == user_id,
            Event.name == FEED_FETCHED,
            Event.created_at >= day_start,
        )
        .limit(1)
    )
    if seen is None:
        await track(session, user_id, FEED_FETCHED, {"items": items})
        await session.commit()


@router.get(
    "/ics/{token}.ics",
    summary="Лента iCalendar пользователя (без initData, доступ по токену из ссылки)",
    response_class=Response,
    responses={
        200: {"content": {"text/calendar": {}}, "description": "Лента RFC 5545"},
        404: {"description": "Неверный токен"},
        503: {"description": "Справочник недоступен"},
    },
)
async def ics_feed(
    token: str,
    session: Session,
    settings: Conf,
    now: Now,
    reference: OptionalRef,
) -> Response:
    user_id = user_from_token(token, settings.max_webhook_secret)
    if user_id is None:
        raise _not_found()
    if reference is None:
        raise HTTPException(status_code=503, detail="reference_unavailable")
    events = await _feed_events(session, user_id, reference, _uid_domain(settings))
    await _track_fetch_daily(session, user_id, len(events))
    body = render_calendar(events, name=CALENDAR_NAME, now=now)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={
            # inline: iOS открывает .ics предпросмотром «Добавить в Календарь», Android скачивает
            "Content-Disposition": f'inline; filename="{FILE_NAME}"',
            "Cache-Control": "private, no-store",
        },
    )
