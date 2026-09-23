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

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.calendar.types import (
    Catalog,
    DateRule,
    NdsConfig,
    Obligation,
    Reference,
    WorkdayCalendar,
)
from app.core.config import Settings


def parse_date_rule(raw: Mapping[str, Any]) -> DateRule:
    """`date_rule` одной записи → YearlyRule | QuarterlyRule | MonthlyInQuarterRule.

    Невалидно — InvalidRecordError(record_id=None, reason); parse_obligation подставит id.
    """
    raise NotImplementedError("T2")


def parse_obligation(raw: Mapping[str, Any]) -> Obligation:
    """Одна запись справочника → Obligation. Невалидна — InvalidRecordError с id и причиной."""
    raise NotImplementedError("T2")


def load_catalog(path: Path) -> Catalog:
    """obligations.yaml → Catalog только из валидных записей; невалидные — в лог и мимо."""
    raise NotImplementedError("T2")


def load_workdays(path: Path) -> WorkdayCalendar:
    """workdays.yaml → WorkdayCalendar. Ключи верхнего уровня — годы, в каждом
    `holidays` и `workdays` (списки дат этого же года). Ошибка формата — ReferenceFileError."""
    raise NotImplementedError("T2")


def load_nds(path: Path) -> NdsConfig:
    """nds.yaml → NdsConfig. Ошибка формата или пустой `thresholds` — ReferenceFileError."""
    raise NotImplementedError("T2")


def load_reference(settings: Settings) -> Reference:
    """Все три файла по путям из настроек."""
    raise NotImplementedError("T2")


def get_reference() -> Reference:
    """Справочники текущего процесса: загружаются один раз при первом вызове и кешируются.

    Этим пользуются сборка (T4), бот и API. Тесты подменяют результат фикстурами
    из backend/tests/fixtures/, а не читают реальные файлы аналитика.
    """
    raise NotImplementedError("T2")
