"""Лимит дохода спецрежима (#107): патент и АУСН, content/regime_limits.yaml.

Типы справочника и проверка «профиль → превышен ли лимит». Загрузка и проверка формата —
`loader.load_regime_limits` / `loader.get_regime_limits`. Лимиты и годы — только из файла,
в коде чисел нет; границы ответов онбординга — `types.INCOME_BAND_BOUNDS_RUB` (D25).

Проверка (`check`):
    режима нет в справочнике (usn6, usn15, unknown)       → unknown
    доход «Не знаю» или ответа ещё нет                     → unknown
    на год дохода лимита нет                                → unknown
    доход по ответу целиком выше лимита                     → exceeded
    доход по ответу целиком не выше лимита                  → ok
    диапазон ответа пересекает лимит                        → unknown
Предупреждение бот показывает только при `exceeded`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.calendar.types import INCOME_BAND_BOUNDS_RUB

LimitStatus = Literal["exceeded", "ok", "unknown"]


@dataclass(frozen=True, slots=True)
class RegimeLimit:
    """Лимит для дохода за любой год из `income_years` или за любой год с `income_years_from`.

    Ровно одно из двух задано: `income_years` непустой или `income_years_from` не None.
    """

    income_years: tuple[int, ...]
    income_years_from: int | None
    limit_rub: int
    verified: bool


@dataclass(frozen=True, slots=True)
class RegimeLimitStep:
    """Один шаг «что делать» при превышении лимита: текст, норма, сверен ли первоисточником."""

    text: str
    norm: str
    verified: bool


@dataclass(frozen=True, slots=True)
class RegimeRules:
    regime: str
    norm: str
    norm_url: str
    fns_url: str
    limits: tuple[RegimeLimit, ...]
    steps: tuple[RegimeLimitStep, ...]


@dataclass(frozen=True, slots=True)
class RegimeLimitsConfig:
    regimes: dict[str, RegimeRules]
    last_checked_at: date


def limit_for(rules: RegimeRules, income_year: int) -> int | None:
    """Лимит в рублях для дохода за `income_year`; None — на этот год лимита нет."""
    for limit in rules.limits:
        if income_year in limit.income_years:
            return limit.limit_rub
        if limit.income_years_from is not None and income_year >= limit.income_years_from:
            return limit.limit_rub
    return None


def check(
    regime: str | None,
    income_band: str | None,
    income_year: int,
    config: RegimeLimitsConfig,
) -> LimitStatus:
    """Превышен ли лимит режима по ответам онбординга (доход за `income_year`)."""
    rules = config.regimes.get(regime or "")
    if rules is None:
        return "unknown"
    bounds = INCOME_BAND_BOUNDS_RUB.get(income_band or "")
    if bounds is None:
        return "unknown"
    limit = limit_for(rules, income_year)
    if limit is None:
        return "unknown"
    low, high = bounds
    if low >= limit:
        return "exceeded"
    if high is not None and high <= limit:
        return "ok"
    return "unknown"
