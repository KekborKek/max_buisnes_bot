"""Шаги «Как сделать» (экраны 7 и 16): раскрытие подстановок в `howto_steps` (D28).

Общее для бота (экран 7) и API мини-аппа (карточка 16), чтобы дата в шагах выглядела одинаково.
"""

import logging
import re
from datetime import date

from app.bot.formatting import format_date
from app.calendar.types import Obligation

log = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def expand_howto_steps(ob: Obligation, due_date: date, today: date) -> list[str]:
    """Раскрывает `{due_date}` в шагах (D28). Прочие `{...}` остаются как есть + warning.

    `{notice_date}` правила не имеет, поэтому тоже остаётся и попадает в лог.
    """
    unknown: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        if match.group(1) == "due_date":
            return format_date(due_date, today)
        unknown.add(match.group(0))
        return match.group(0)

    steps = [_PLACEHOLDER.sub(repl, step) for step in ob.howto_steps]
    if unknown:
        log.warning(
            "Обязательство %s: в howto_steps нераскрытые подстановки %s",
            ob.id,
            ", ".join(sorted(unknown)),
        )
    return steps
