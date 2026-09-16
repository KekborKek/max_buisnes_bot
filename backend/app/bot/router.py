"""Маршрутизация апдейтов по обработчикам.

Регистрация:
    @router.on("bot_started")                      — по типу апдейта
    @router.on_callback("menu:")                    — по префиксу payload кнопки
    @router.on_state("ask_inn")                     — текст, когда пользователь в состоянии FSM
    @router.on_text("/help")                        — точный текст/команда
    @router.fallback                                — если ничего не подошло
"""

import logging
from collections.abc import Awaitable, Callable

from app.bot.context import Ctx

log = logging.getLogger(__name__)
Handler = Callable[[Ctx], Awaitable[None]]


class Router:
    def __init__(self) -> None:
        self._by_type: dict[str, Handler] = {}
        self._by_callback: list[tuple[str, Handler]] = []
        self._by_state: dict[str, Handler] = {}
        self._by_text: dict[str, Handler] = {}
        self._fallback: Handler | None = None

    def on(self, update_type: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._by_type[update_type] = fn
            return fn

        return deco

    def on_callback(self, prefix: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._by_callback.append((prefix, fn))
            return fn

        return deco

    def on_state(self, state: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._by_state[state] = fn
            return fn

        return deco

    def on_text(self, text: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._by_text[text.strip().lower()] = fn
            return fn

        return deco

    def fallback(self, fn: Handler) -> Handler:
        self._fallback = fn
        return fn

    async def resolve(self, ctx: Ctx) -> Handler | None:
        if ctx.update_type == "message_callback" and ctx.payload:
            for prefix, fn in sorted(self._by_callback, key=lambda x: -len(x[0])):
                if ctx.payload.startswith(prefix):
                    return fn
        if ctx.update_type == "message_created":
            if ctx.text and ctx.text.strip().lower() in self._by_text:
                return self._by_text[ctx.text.strip().lower()]
            state, _ = await ctx.get_state()
            if state and state in self._by_state:
                return self._by_state[state]
        if ctx.update_type in self._by_type:
            return self._by_type[ctx.update_type]
        return self._fallback


router = Router()
