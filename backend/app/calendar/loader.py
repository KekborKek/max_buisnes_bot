"""Загрузка и валидация справочников (задача T2). Сейчас — только контракт.

Правила (data-model.md §1):
- запись obligations.yaml без любого обязательного поля, с `howto_steps` не из трёх строк,
  неизвестным значением `category`/`shift`/`date_rule.type` или неизвестным ключом
  `applies_if` не попадает в продукт: id и причина — в лог (warning), старт не падает;
- повтор id — вторая запись пропускается так же;
- файл целиком отсутствует или не парсится — ReferenceFileError;
- даты в YAML — строки "YYYY-MM-DD" (или date, если PyYAML разобрал сам).
Пути к файлам — Settings.obligations_path / workdays_path / nds_path.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from app.calendar.types import (
    CATEGORIES,
    INCOME_BANDS,
    REGIMES,
    SHIFTS,
    AppliesIf,
    Catalog,
    DateRule,
    HowtoLink,
    InvalidRecordError,
    MonthlyInQuarterRule,
    NdsConfig,
    NdsThreshold,
    Obligation,
    QuarterlyRule,
    Reference,
    ReferenceFileError,
    WorkdayCalendar,
    YearlyRule,
    YearWorkdays,
)
from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)

_APPLIES_IF_LIST_CHOICES: dict[str, frozenset[str]] = {
    "regime": REGIMES,
    "income_band": INCOME_BANDS,
}
_APPLIES_IF_BOOL_KEYS = ("has_employees", "nds_payer")
_APPLIES_IF_KEYS = frozenset({*_APPLIES_IF_LIST_CHOICES, *_APPLIES_IF_BOOL_KEYS})


# --- Общие помощники ------------------------------------------------------------------------


def _coerce_date(value: Any) -> date | None:
    """Строка "YYYY-MM-DD" или уже разобранный PyYAML `date`/`datetime` → `date`."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _load_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReferenceFileError(f"Не удалось прочитать {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ReferenceFileError(f"Не удалось разобрать {path}: {exc}") from exc


# --- obligations.yaml -------------------------------------------------------------------------


def _require(raw: Mapping[str, Any], key: str, record_id: str | None) -> Any:
    if key not in raw or raw[key] is None:
        raise InvalidRecordError(record_id, f"обязательное поле {key} отсутствует")
    return raw[key]


def _require_str(
    raw: Mapping[str, Any], key: str, record_id: str | None, *, label: str | None = None
) -> str:
    value = _require(raw, key, record_id)
    name = label or key
    if not isinstance(value, str) or not value.strip():
        raise InvalidRecordError(record_id, f"поле {name} должно быть непустой строкой")
    return value


def _require_bool(raw: Mapping[str, Any], key: str, record_id: str | None) -> bool:
    value = _require(raw, key, record_id)
    if not isinstance(value, bool):
        raise InvalidRecordError(record_id, f"поле {key} должно быть true или false")
    return value


def _require_int(raw: Mapping[str, Any], key: str, record_id: str | None) -> int:
    value = _require(raw, key, record_id)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRecordError(record_id, f"поле {key} должно быть целым числом")
    return value


def _require_choice(
    raw: Mapping[str, Any], key: str, record_id: str | None, choices: frozenset[str]
) -> str:
    value = _require_str(raw, key, record_id)
    if value not in choices:
        raise InvalidRecordError(record_id, f"поле {key} имеет недопустимое значение: {value!r}")
    return value


def _int_in_range(raw: Mapping[str, Any], key: str, lo: int, hi: int, *, label: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRecordError(None, f"{label} должно быть целым числом")
    if not (lo <= value <= hi):
        raise InvalidRecordError(None, f"{label} вне диапазона {lo}..{hi}: {value}")
    return value


def parse_date_rule(raw: Mapping[str, Any]) -> DateRule:
    """`date_rule` одной записи → YearlyRule | QuarterlyRule | MonthlyInQuarterRule.

    Невалидно — InvalidRecordError(record_id=None, reason); parse_obligation подставит id.
    """
    if not isinstance(raw, Mapping):
        raise InvalidRecordError(None, "date_rule должен быть словарём")

    rule_type = raw.get("type")
    if rule_type == "yearly":
        month = _int_in_range(raw, "month", 1, 12, label="date_rule.month")
        day = _int_in_range(raw, "day", 1, 31, label="date_rule.day")
        return YearlyRule(month=month, day=day)

    if rule_type == "quarterly":
        offset_month = _int_in_range(raw, "offset_month", 1, 3, label="date_rule.offset_month")
        day = _int_in_range(raw, "day", 1, 31, label="date_rule.day")
        return QuarterlyRule(offset_month=offset_month, day=day)

    if rule_type == "monthly_in_quarter":
        months_raw = raw.get("months")
        if not isinstance(months_raw, list) or not months_raw:
            raise InvalidRecordError(None, "date_rule.months должен быть непустым списком")
        if any(isinstance(m, bool) or not isinstance(m, int) for m in months_raw):
            raise InvalidRecordError(None, "date_rule.months должен состоять из целых чисел")
        months = tuple(int(m) for m in months_raw)
        if any(m < 1 or m > 3 for m in months):
            raise InvalidRecordError(None, "date_rule.months: каждое значение должно быть 1..3")
        if list(months) != sorted(months) or len(set(months)) != len(months):
            raise InvalidRecordError(
                None, "date_rule.months должны идти по возрастанию без повторов"
            )
        day = _int_in_range(raw, "day", 1, 31, label="date_rule.day")
        return MonthlyInQuarterRule(months=months, day=day)

    raise InvalidRecordError(None, f"date_rule.type неизвестен: {rule_type!r}")


def _parse_applies_if(raw: Any, record_id: str | None) -> AppliesIf:
    if not isinstance(raw, Mapping):
        raise InvalidRecordError(record_id, "поле applies_if должно быть словарём")

    unknown = set(raw.keys()) - _APPLIES_IF_KEYS
    if unknown:
        key = sorted(unknown)[0]
        raise InvalidRecordError(record_id, f"applies_if: неизвестный ключ {key!r}")

    values: dict[str, Any] = {}
    for key, choices in _APPLIES_IF_LIST_CHOICES.items():
        raw_value = raw.get(key)
        if raw_value is None:
            values[key] = None
            continue
        if not isinstance(raw_value, list) or not raw_value:
            raise InvalidRecordError(record_id, f"applies_if.{key} должен быть непустым списком")
        parsed = frozenset(str(v) for v in raw_value)
        unknown_values = parsed - choices
        if unknown_values:
            bad = sorted(unknown_values)[0]
            raise InvalidRecordError(record_id, f"applies_if.{key}: неизвестное значение {bad!r}")
        values[key] = parsed

    for key in _APPLIES_IF_BOOL_KEYS:
        raw_value = raw.get(key)
        if raw_value is None:
            values[key] = None
            continue
        if not isinstance(raw_value, bool):
            raise InvalidRecordError(record_id, f"applies_if.{key} должен быть true или false")
        values[key] = raw_value

    return AppliesIf(
        regime=values["regime"],
        income_band=values["income_band"],
        has_employees=values["has_employees"],
        nds_payer=values["nds_payer"],
    )


def _parse_howto_steps(raw: Any, record_id: str | None) -> tuple[str, str, str]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise InvalidRecordError(record_id, "howto_steps должен содержать ровно 3 строки")
    steps: list[str] = []
    for step in raw:
        if not isinstance(step, str) or not step.strip():
            raise InvalidRecordError(record_id, "howto_steps: каждый шаг — непустая строка")
        steps.append(step)
    return (steps[0], steps[1], steps[2])


def _parse_howto_link(raw: Any, record_id: str | None) -> HowtoLink:
    if not isinstance(raw, Mapping):
        raise InvalidRecordError(record_id, "howto_link должен быть словарём")
    label = _require_str(raw, "label", record_id, label="howto_link.label")
    url = _require_str(raw, "url", record_id, label="howto_link.url")
    return HowtoLink(label=label, url=url)


def parse_obligation(raw: Mapping[str, Any]) -> Obligation:
    """Одна запись справочника → Obligation. Невалидна — InvalidRecordError с id и причиной."""
    if not isinstance(raw, Mapping):
        raise InvalidRecordError(None, "запись справочника должна быть словарём")

    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise InvalidRecordError(None, "поле id отсутствует или пустое")
    record_id = raw_id

    title = _require_str(raw, "title", record_id)
    category = _require_choice(raw, "category", record_id, CATEGORIES)

    date_rule_raw = _require(raw, "date_rule", record_id)
    try:
        date_rule = parse_date_rule(date_rule_raw)
    except InvalidRecordError as exc:
        raise InvalidRecordError(record_id, exc.reason) from exc

    shift = _require_choice(raw, "shift", record_id, SHIFTS)

    applies_if_raw = _require(raw, "applies_if", record_id)
    applies_if = _parse_applies_if(applies_if_raw, record_id)

    needs_prep = _require_bool(raw, "needs_prep", record_id)
    norm = _require_str(raw, "norm", record_id)
    source_url = _require_str(raw, "source_url", record_id)

    howto_steps_raw = _require(raw, "howto_steps", record_id)
    howto_steps = _parse_howto_steps(howto_steps_raw, record_id)

    howto_link_raw = _require(raw, "howto_link", record_id)
    howto_link = _parse_howto_link(howto_link_raw, record_id)

    penalty_text = _require_str(raw, "penalty_text", record_id)
    rule_version = _require_int(raw, "rule_version", record_id)

    last_checked_raw = _require(raw, "last_checked_at", record_id)
    last_checked_at = _coerce_date(last_checked_raw)
    if last_checked_at is None:
        raise InvalidRecordError(
            record_id, f"поле last_checked_at не является датой YYYY-MM-DD: {last_checked_raw!r}"
        )

    return Obligation(
        id=record_id,
        title=title,
        category=category,  # type: ignore[arg-type]
        date_rule=date_rule,
        shift=shift,  # type: ignore[arg-type]
        applies_if=applies_if,
        needs_prep=needs_prep,
        norm=norm,
        source_url=source_url,
        howto_steps=howto_steps,
        howto_link=howto_link,
        penalty_text=penalty_text,
        rule_version=rule_version,
        last_checked_at=last_checked_at,
    )


def load_catalog(path: Path) -> Catalog:
    """obligations.yaml → Catalog только из валидных записей; невалидные — в лог и мимо."""
    data = _load_yaml(path)
    if not isinstance(data, Mapping):
        raise ReferenceFileError(f"{path}: справочник должен быть словарём")

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ReferenceFileError(f"{path}: поле version отсутствует или пустое")

    raw_obligations = data.get("obligations")
    if not isinstance(raw_obligations, list):
        raise ReferenceFileError(f"{path}: поле obligations должно быть списком")

    obligations: list[Obligation] = []
    seen_ids: set[str] = set()
    for raw in raw_obligations:
        try:
            obligation = parse_obligation(raw)
        except InvalidRecordError as exc:
            log.warning(
                "Пропущена запись справочника обязательств %s: %s",
                exc.record_id or "<без id>",
                exc.reason,
            )
            continue
        if obligation.id in seen_ids:
            log.warning(
                "Пропущена запись справочника обязательств %s: повторяет id уже загруженной записи",
                obligation.id,
            )
            continue
        seen_ids.add(obligation.id)
        obligations.append(obligation)

    return Catalog(version=version, obligations=tuple(obligations))


# --- workdays.yaml -----------------------------------------------------------------------------


def _parse_workday_dates(raw: Any, path: Path, year: int, field: str) -> frozenset[date]:
    if not isinstance(raw, list):
        raise ReferenceFileError(f"{path}: {year}.{field} должен быть списком дат")
    result: set[date] = set()
    for item in raw:
        parsed = _coerce_date(item)
        if parsed is None:
            raise ReferenceFileError(f"{path}: {year}.{field}: {item!r} — не дата YYYY-MM-DD")
        if parsed.year != year:
            raise ReferenceFileError(
                f"{path}: {year}.{field}: дата {parsed.isoformat()} относится не к {year} году"
            )
        result.add(parsed)
    return frozenset(result)


def load_workdays(path: Path) -> WorkdayCalendar:
    """workdays.yaml → WorkdayCalendar. Ключи верхнего уровня — годы, в каждом
    `holidays` и `workdays` (списки дат этого же года). Ошибка формата — ReferenceFileError."""
    data = _load_yaml(path)
    if not isinstance(data, Mapping):
        raise ReferenceFileError(f"{path}: производственный календарь должен быть словарём")

    years: dict[int, YearWorkdays] = {}
    for raw_year, raw_entry in data.items():
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            raise ReferenceFileError(f"{path}: ключ {raw_year!r} не является годом") from None
        if not isinstance(raw_entry, Mapping):
            raise ReferenceFileError(f"{path}: {year}: запись должна быть словарём")

        holidays = _parse_workday_dates(raw_entry.get("holidays", []), path, year, "holidays")
        workdays = _parse_workday_dates(raw_entry.get("workdays", []), path, year, "workdays")

        overlap = holidays & workdays
        if overlap:
            bad = sorted(overlap)[0].isoformat()
            raise ReferenceFileError(
                f"{path}: {year}: дата {bad} одновременно в holidays и workdays"
            )

        years[year] = YearWorkdays(holidays=holidays, workdays=workdays)

    return WorkdayCalendar(years=years)


# --- nds.yaml ------------------------------------------------------------------------------------


def load_nds(path: Path) -> NdsConfig:
    """nds.yaml → NdsConfig. Ошибка формата или пустой `thresholds` — ReferenceFileError."""
    data = _load_yaml(path)
    if not isinstance(data, Mapping):
        raise ReferenceFileError(f"{path}: nds.yaml должен быть словарём")

    law = data.get("law")
    law_url = data.get("law_url")
    fns_guide_url = data.get("fns_guide_url")
    if not all(isinstance(v, str) and v.strip() for v in (law, law_url, fns_guide_url)):
        raise ReferenceFileError(
            f"{path}: поля law, law_url, fns_guide_url обязательны и не должны быть пустыми"
        )

    raw_thresholds = data.get("thresholds")
    if not isinstance(raw_thresholds, list) or not raw_thresholds:
        raise ReferenceFileError(f"{path}: поле thresholds должно быть непустым списком")

    thresholds: list[NdsThreshold] = []
    seen_income_years: dict[int, int] = {}
    for index, raw in enumerate(raw_thresholds):
        if not isinstance(raw, Mapping):
            raise ReferenceFileError(f"{path}: thresholds: запись должна быть словарём")
        income_years_raw = raw.get("income_years")
        if not isinstance(income_years_raw, list) or not income_years_raw:
            raise ReferenceFileError(
                f"{path}: thresholds: income_years должен быть непустым списком"
            )
        if any(isinstance(y, bool) or not isinstance(y, int) for y in income_years_raw):
            raise ReferenceFileError(f"{path}: thresholds: income_years должен состоять из чисел")
        income_years = tuple(int(y) for y in income_years_raw)
        for year in income_years:
            if year in seen_income_years:
                raise ReferenceFileError(
                    f"{path}: thresholds: год {year} встречается в income_years "
                    f"порогов {seen_income_years[year]} и {index}"
                )
            seen_income_years[year] = index

        limit_rub = raw.get("limit_rub")
        if isinstance(limit_rub, bool) or not isinstance(limit_rub, int):
            raise ReferenceFileError(f"{path}: thresholds: limit_rub должен быть целым числом")

        thresholds.append(NdsThreshold(income_years=income_years, limit_rub=limit_rub))

    last_checked_at = _coerce_date(data.get("last_checked_at"))
    if last_checked_at is None:
        raise ReferenceFileError(
            f"{path}: поле last_checked_at обязательно и должно быть датой YYYY-MM-DD"
        )

    return NdsConfig(
        law=law,
        law_url=law_url,
        fns_guide_url=fns_guide_url,
        thresholds=tuple(thresholds),
        last_checked_at=last_checked_at,
    )


# --- Сборка -----------------------------------------------------------------------------------


def load_reference(settings: Settings) -> Reference:
    """Все три файла по путям из настроек."""
    return Reference(
        catalog=load_catalog(settings.obligations_path),
        workdays=load_workdays(settings.workdays_path),
        nds=load_nds(settings.nds_path),
    )


@cache
def get_reference() -> Reference:
    """Справочники текущего процесса: загружаются один раз при первом вызове и кешируются.

    Этим пользуются сборка (T4), бот и API. Тесты подменяют результат фикстурами
    из backend/tests/fixtures/, а не читают реальные файлы аналитика.
    """
    return load_reference(get_settings())
