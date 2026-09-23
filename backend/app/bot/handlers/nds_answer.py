"""Экран 3 «Ответ про НДС» — ЗАГЛУШКА, заменяется в T5b.

Единственная точка входа, которую зовёт онбординг после четвёртого ответа
(`onboarding.complete`). T5b заменяет тело функции, сигнатуру оставляет.
"""

from app.bot.context import Ctx
from app.core.models import Profile
from app.core.texts import t


async def show_nds_answer(ctx: Ctx, profile: Profile, *, regime_guessed: bool) -> None:
    """Заменяется в T5b.

    `profile` — уже с `nds_payer`; `regime_guessed` — режим был «Не знаю» и поставлен
    `usn6` (экран 3 тогда добавляет `nds.regime_guessed`).
    """
    await ctx.reply(t("onboarding.completed_stub"))
