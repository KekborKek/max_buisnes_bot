"""DATA-1 (#68): CI-проверка настоящих справочников аналитика.

Это ЕДИНСТВЕННЫЙ тест в проекте, который читает реальные файлы из `content/`, а не
фикстуры из `backend/tests/fixtures/` (обычное правило проекта — см. `docs/prompts/
executor.md`, «в тестах — фикстуры, не реальные файлы аналитика»). Нарушение осознанное
и ограничено этим одним файлом: до сих пор все тесты работают на фикстурах и ни один не
читает `content/obligations.yaml`, `content/workdays.yaml`, `content/nds.yaml` — значит,
опечатка аналитика в реальном справочнике проходит CI зелёной и на проде бот либо отвечает
`common.error` на `/start` (сборка календаря падает), либо молча теряет запись (загрузчик
по конструкции пропускает невалидные записи в лог, не роняя старт — см. `loader.py`).
Этот файл ловит такие опечатки до продакшена.

Справочник форматов — `docs/spec/data-model.md` §1, решения D17 (usn6/usn15 — разные
записи), D22 (горизонт сборки — 31 декабря следующего года), D28 (в `howto_steps`
раскрывается только `{due_date}`), D29 (несуществующее число месяца → последний день
месяца, это уже проверяет `dates.py`, здесь не дублируется).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.calendar import loader
from app.calendar.build import build_horizon
from app.calendar.dates import nds_limit_for, obligation_dates
from app.calendar.types import REGIMES, MissingYearError

ROOT = Path(__file__).resolve().parents[2]
CONTENT_DIR = ROOT / "content"
OBLIGATIONS_PATH = CONTENT_DIR / "obligations.yaml"
WORKDAYS_PATH = CONTENT_DIR / "workdays.yaml"
NDS_PATH = CONTENT_DIR / "nds.yaml"

LOGGER_NAME = "app.calendar.loader"

_PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")

# Кнопки вопроса о режиме на экране онбординга (content/texts.yaml: q2_usn6, q2_usn15,
# q2_patent, q2_ausn) — реальные ответы пользователя. `unknown` в REGIMES — не кнопка,
# а сентинел «ещё не ответил»; календарь на нём не собирается, поэтому не проверяем.
_ONBOARDING_REGIMES = sorted(REGIMES - {"unknown"})


def _load_catalog():
    """Настоящий загрузчик, реальный файл — не фикстура (см. докстринг модуля)."""
    return loader.load_catalog(OBLIGATIONS_PATH)


def _load_workdays():
    return loader.load_workdays(WORKDAYS_PATH)


def _load_nds():
    return loader.load_nds(NDS_PATH)


# --- 1. Ни одна запись обязательств не потеряна при загрузке, id уникальны -----------------


def test_all_obligations_loaded_without_skips(caplog):
    """content/obligations.yaml целиком доходит до Catalog: загрузчик не пропустил запись.

    Если файла нет вовсе — load_catalog поднимет ReferenceFileError и тест упадёт
    (не пропустится), что и требуется: без файла прод не работает.
    """
    raw = yaml.safe_load(OBLIGATIONS_PATH.read_text(encoding="utf-8"))
    raw_obligations = raw["obligations"]
    raw_ids = [record["id"] for record in raw_obligations]
    assert len(raw_ids) == len(set(raw_ids)), (
        "content/obligations.yaml: дублирующийся id в сыром файле — загрузчик тихо "
        "оставит только первую запись"
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(OBLIGATIONS_PATH)

    assert caplog.messages == [], (
        f"загрузчик пропустил запись(и) справочника — см. предупреждения: {caplog.messages}"
    )
    assert len(catalog.obligations) == len(raw_obligations), (
        f"в файле {len(raw_obligations)} записей, загрузилось {len(catalog.obligations)} — "
        "часть пропала без предупреждения в логе"
    )
    loaded_ids = [ob.id for ob in catalog.obligations]
    assert len(loaded_ids) == len(set(loaded_ids)), "id обязательств не уникальны после загрузки"


# --- 2. Даты и перенос на рабочий день считаются по календарю, а не наугад -----------------


def test_dates_computed_within_known_years():
    """Для каждого обязательства и каждого года из workdays.yaml даты считаются без
    исключений, а перенос на рабочий день не требует года, которого в файле нет —
    вплоть до горизонта сборки (build_horizon от 1 января первого года справочника)."""
    catalog = _load_catalog()
    workdays = _load_workdays()
    assert workdays.years, "content/workdays.yaml: нет ни одного года"

    min_year = min(workdays.years)
    horizon = build_horizon(date(min_year, 1, 1))

    for year in range(min_year, horizon.year + 1):
        for ob in catalog.obligations:
            try:
                dues = obligation_dates(ob, year, workdays)
            except MissingYearError as exc:
                pytest.fail(
                    f"{ob.id}: расчёт дат за {year} год требует данных из workdays.yaml, "
                    f"которых там нет ({exc})"
                )
                continue
            for due in dues:
                if due.due_date <= horizon and due.due_date.year not in workdays.years:
                    pytest.fail(
                        f"{ob.id}: original_date={due.original_date} после переноса на "
                        f"рабочий день попадает в {due.due_date.year} год, которого нет "
                        "в content/workdays.yaml — перенос считается наугад"
                    )


# --- 3. В howto_steps нет плейсхолдеров, кроме {due_date} (D28) ---------------------------


def test_howto_steps_only_due_date_placeholder():
    catalog = _load_catalog()
    for ob in catalog.obligations:
        for step in ob.howto_steps:
            for placeholder in _PLACEHOLDER_RE.findall(step):
                assert placeholder == "{due_date}", (
                    f"{ob.id}: howto_steps использует подстановку {placeholder!r}, "
                    "а бэкенд по D28 раскрывает только {due_date} — она останется в "
                    "тексте как есть"
                )


# --- 4. В nds.yaml есть порог для доходов текущего и прошлого года ------------------------


def test_nds_thresholds_cover_current_and_previous_year():
    nds = _load_nds()
    today = date.today()
    for income_year in (today.year, today.year - 1):
        assert nds_limit_for(income_year, nds) is not None, (
            f"content/nds.yaml: нет порога НДС для дохода за {income_year} год "
            "(считается от date.today())"
        )


# --- 5. У каждого варианта ответа онбординга о режиме есть хотя бы одна запись ------------


def _regime_param(regime: str):
    if regime == "ausn":
        # НАХОДКА (не чинить, вопрос аналитику): content/texts.yaml предлагает кнопку
        # q2_ausn на онбординге, но ни одна запись content/obligations.yaml не указывает
        # ausn в applies_if.regime. Профиль с этим режимом получает пустой календарь
        # (экран calendar_ready.empty — это не падение, но, возможно, пробел в данных).
        # strict=True: как только аналитик добавит запись(и) для АУСН, xfail станет XPASS
        # и упадёт с ошибкой — это и есть сигнал снять пометку.
        return pytest.param(
            regime,
            marks=pytest.mark.xfail(
                strict=True,
                reason="в content/obligations.yaml нет записей с applies_if.regime, "
                "включающим ausn — вопрос аналитику (issue #68)",
            ),
        )
    return regime


@pytest.mark.parametrize("regime", [_regime_param(r) for r in _ONBOARDING_REGIMES])
def test_each_onboarding_regime_has_obligations(regime):
    """Профиль после онбординга не должен получать пустой календарь ни для одного
    реального ответа на вопрос о режиме (content/texts.yaml: q2_usn6/q2_usn15/q2_patent/
    q2_ausn)."""
    catalog = _load_catalog()
    matching = [
        ob
        for ob in catalog.obligations
        if ob.applies_if.regime is None or regime in ob.applies_if.regime
    ]
    assert matching, (
        f"ни одна запись справочника не применяется к режиму {regime!r} — "
        "профиль после онбординга получит пустой календарь"
    )
