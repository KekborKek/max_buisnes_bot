"""T2 (#39): загрузчик и валидатор справочников (obligations/workdays/nds)."""

import logging
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.calendar import loader
from app.calendar.types import (
    Catalog,
    InvalidRecordError,
    NdsConfig,
    Reference,
    ReferenceFileError,
    WorkdayCalendar,
)
from app.core.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
LOGGER_NAME = "app.calendar.loader"


def _valid_obligation() -> dict:
    """ТЕСТОВЫЕ ДАННЫЕ: минимальная валидная запись для юнит-тестов parse/load."""
    return {
        "id": "unit_test_obligation",
        "title": "Юнит-тестовое обязательство",
        "category": "taxes",
        "date_rule": {"type": "yearly", "month": 6, "day": 15},
        "shift": "next_workday",
        "applies_if": {"regime": ["usn6"]},
        "needs_prep": True,
        "norm": "Тестовая норма",
        "source_url": "https://example.invalid/norm",
        "howto_steps": ["Шаг 1", "Шаг 2", "Шаг 3"],
        "howto_link": {"label": "Открыть", "url": "https://example.invalid"},
        "penalty_text": "Тестовый текст",
        "rule_version": 1,
        "last_checked_at": "2026-01-01",
    }


def _write_catalog(tmp_path: Path, obligations: list[dict], *, version: str = "2026-01-01") -> Path:
    path = tmp_path / "obligations.yaml"
    path.write_text(
        yaml.safe_dump({"version": version, "obligations": obligations}, allow_unicode=True),
        encoding="utf-8",
    )
    return path


# --- parse_date_rule ---------------------------------------------------------------------------


def test_parse_date_rule_yearly():
    rule = loader.parse_date_rule({"type": "yearly", "month": 10, "day": 28})
    assert rule.type == "yearly"
    assert rule.month == 10
    assert rule.day == 28


def test_parse_date_rule_quarterly():
    rule = loader.parse_date_rule({"type": "quarterly", "offset_month": 1, "day": 25})
    assert rule.type == "quarterly"
    assert rule.offset_month == 1
    assert rule.day == 25


def test_parse_date_rule_monthly_in_quarter():
    rule = loader.parse_date_rule({"type": "monthly_in_quarter", "months": [1, 2, 3], "day": 28})
    assert rule.type == "monthly_in_quarter"
    assert rule.months == (1, 2, 3)
    assert rule.day == 28


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "yearly", "month": 13, "day": 1},
        {"type": "yearly", "month": 1, "day": 32},
        {"type": "yearly", "month": 1},
        {"type": "yearly", "month": "октябрь", "day": 1},
        {"type": "quarterly", "offset_month": 4, "day": 1},
        {"type": "quarterly", "offset_month": 0, "day": 1},
        {"type": "monthly_in_quarter", "months": [], "day": 1},
        {"type": "monthly_in_quarter", "months": [1, 4], "day": 1},
        {"type": "monthly_in_quarter", "months": [3, 1, 2], "day": 1},
        {"type": "monthly_in_quarter", "months": [1, 1, 2], "day": 1},
        {"type": "weekly", "day": 1},
        {},
        "не словарь",
    ],
)
def test_parse_date_rule_invalid(raw):
    with pytest.raises(InvalidRecordError) as exc_info:
        loader.parse_date_rule(raw)
    assert exc_info.value.record_id is None


# --- parse_obligation / load_catalog: обязательные поля -----------------------------------------

REQUIRED_FIELDS = [
    "title",
    "category",
    "date_rule",
    "shift",
    "applies_if",
    "needs_prep",
    "norm",
    "source_url",
    "howto_steps",
    "howto_link",
    "penalty_text",
    "rule_version",
    "last_checked_at",
]


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_load_catalog_skips_record_missing_required_field(tmp_path, caplog, field):
    record = _valid_obligation()
    del record[field]
    path = _write_catalog(tmp_path, [record])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(path)

    assert catalog.obligations == ()
    assert any(
        "unit_test_obligation" in message and field in message for message in caplog.messages
    )


def test_load_catalog_skips_record_missing_id(tmp_path, caplog):
    record = _valid_obligation()
    del record["id"]
    path = _write_catalog(tmp_path, [record])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(path)

    assert catalog.obligations == ()
    assert any("<без id>" in message and "id" in message for message in caplog.messages)


def test_load_catalog_skips_record_with_blank_id(tmp_path, caplog):
    record = _valid_obligation()
    record["id"] = "   "
    path = _write_catalog(tmp_path, [record])

    catalog = loader.load_catalog(path)

    assert catalog.obligations == ()


# --- Правила валидации отдельных полей ----------------------------------------------------------


@pytest.mark.parametrize("steps", [["один", "два"], ["1", "2", "3", "4"], [], ["", "два", "три"]])
def test_load_catalog_skips_invalid_howto_steps(tmp_path, caplog, steps):
    record = _valid_obligation()
    record["howto_steps"] = steps
    path = _write_catalog(tmp_path, [record])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(path)

    assert catalog.obligations == ()
    assert any(
        "unit_test_obligation" in message and "howto_steps" in message
        for message in caplog.messages
    )


def test_load_catalog_skips_unknown_category(tmp_path):
    record = _valid_obligation()
    record["category"] = "no_such_category"
    path = _write_catalog(tmp_path, [record])

    assert loader.load_catalog(path).obligations == ()


def test_load_catalog_skips_unknown_shift(tmp_path):
    record = _valid_obligation()
    record["shift"] = "sometime"
    path = _write_catalog(tmp_path, [record])

    assert loader.load_catalog(path).obligations == ()


def test_load_catalog_skips_invalid_date_rule_and_logs_id(tmp_path, caplog):
    record = _valid_obligation()
    record["date_rule"] = {"type": "yearly", "month": 13, "day": 1}
    path = _write_catalog(tmp_path, [record])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(path)

    assert catalog.obligations == ()
    assert any(
        "unit_test_obligation" in message and "date_rule" in message for message in caplog.messages
    )


def test_load_catalog_skips_unknown_applies_if_key(tmp_path, caplog):
    record = _valid_obligation()
    record["applies_if"] = {"unknown_key": True}
    path = _write_catalog(tmp_path, [record])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(path)

    assert catalog.obligations == ()
    assert any("applies_if" in message for message in caplog.messages)


@pytest.mark.parametrize(
    "applies_if",
    [
        {"regime": ["not_a_regime"]},
        {"income_band": ["not_a_band"]},
        {"has_employees": "yes"},
        {"nds_payer": "no"},
        {"regime": []},
    ],
)
def test_load_catalog_skips_invalid_applies_if_value(tmp_path, applies_if):
    record = _valid_obligation()
    record["applies_if"] = applies_if
    path = _write_catalog(tmp_path, [record])

    assert loader.load_catalog(path).obligations == ()


def test_load_catalog_allows_empty_applies_if(tmp_path):
    record = _valid_obligation()
    record["applies_if"] = {}
    path = _write_catalog(tmp_path, [record])

    catalog = loader.load_catalog(path)

    assert len(catalog.obligations) == 1
    assert catalog.obligations[0].applies_if.regime is None


def test_load_catalog_skips_duplicate_id(tmp_path, caplog):
    first = _valid_obligation()
    second = _valid_obligation()
    second["title"] = "Другая запись с тем же id"
    path = _write_catalog(tmp_path, [first, second])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        catalog = loader.load_catalog(path)

    assert len(catalog.obligations) == 1
    assert catalog.obligations[0].title == first["title"]
    assert any(
        "unit_test_obligation" in message and "повтор" in message for message in caplog.messages
    )


def test_load_catalog_skips_non_dict_record(tmp_path):
    path = tmp_path / "obligations.yaml"
    path.write_text(
        yaml.safe_dump({"version": "2026-01-01", "obligations": ["не словарь"]}),
        encoding="utf-8",
    )

    assert loader.load_catalog(path).obligations == ()


# --- Успешная загрузка фикстур --------------------------------------------------------------


def test_load_catalog_loads_fixture():
    catalog = loader.load_catalog(FIXTURES / "obligations.yaml")

    assert catalog.version == "2026-01-01"
    assert {o.id for o in catalog.obligations} == {"test_yearly", "test_quarterly"}
    assert {o.date_rule.type for o in catalog.obligations} == {"yearly", "quarterly"}
    assert any(o.needs_prep for o in catalog.obligations)
    assert any(not o.needs_prep for o in catalog.obligations)


def test_load_workdays_loads_fixture():
    calendar = loader.load_workdays(FIXTURES / "workdays.yaml")

    assert set(calendar.years) == {2026, 2027}
    y2026 = calendar.years[2026]
    assert date(2026, 1, 1) in y2026.holidays
    assert date(2026, 1, 10) in y2026.workdays
    assert not (y2026.holidays & y2026.workdays)


def test_load_nds_loads_fixture():
    config = loader.load_nds(FIXTURES / "nds.yaml")

    assert config.thresholds[0].limit_rub == 20_000_000
    assert config.thresholds[1].income_years == (2027,)
    assert config.last_checked_at == date(2026, 1, 1)


def test_load_reference_loads_all_fixtures():
    settings = Settings(
        _env_file=None,
        content_dir=str(FIXTURES),
        obligations_file="obligations.yaml",
        workdays_file="workdays.yaml",
        nds_file="nds.yaml",
    )

    reference = loader.load_reference(settings)

    assert isinstance(reference, Reference)
    assert len(reference.catalog.obligations) == 2
    assert set(reference.workdays.years) == {2026, 2027}
    assert reference.nds.thresholds


# --- ReferenceFileError: файл целиком отсутствует или не парсится -------------------------------


def test_load_catalog_missing_file_raises(tmp_path):
    with pytest.raises(ReferenceFileError):
        loader.load_catalog(tmp_path / "missing.yaml")


def test_load_catalog_malformed_yaml_raises(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("obligations: [unterminated", encoding="utf-8")

    with pytest.raises(ReferenceFileError):
        loader.load_catalog(path)


def test_load_catalog_not_a_mapping_raises(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")

    with pytest.raises(ReferenceFileError):
        loader.load_catalog(path)


def test_load_catalog_missing_obligations_key_raises(tmp_path):
    path = tmp_path / "obligations.yaml"
    path.write_text("version: '2026-01-01'\n", encoding="utf-8")

    with pytest.raises(ReferenceFileError):
        loader.load_catalog(path)


def test_load_workdays_missing_file_raises(tmp_path):
    with pytest.raises(ReferenceFileError):
        loader.load_workdays(tmp_path / "missing.yaml")


def test_load_workdays_malformed_yaml_raises(tmp_path):
    path = tmp_path / "workdays.yaml"
    path.write_text("2026: [unterminated", encoding="utf-8")

    with pytest.raises(ReferenceFileError):
        loader.load_workdays(path)


def test_load_workdays_date_outside_year_raises(tmp_path):
    path = tmp_path / "workdays.yaml"
    path.write_text("2026:\n  holidays: ['2027-01-01']\n  workdays: []\n", encoding="utf-8")

    with pytest.raises(ReferenceFileError):
        loader.load_workdays(path)


def test_load_workdays_overlap_raises(tmp_path):
    path = tmp_path / "workdays.yaml"
    path.write_text(
        "2026:\n  holidays: ['2026-01-10']\n  workdays: ['2026-01-10']\n", encoding="utf-8"
    )

    with pytest.raises(ReferenceFileError):
        loader.load_workdays(path)


def test_load_workdays_accepts_quoted_and_unquoted_dates(tmp_path):
    path = tmp_path / "workdays.yaml"
    path.write_text(
        "2026:\n  holidays: [2026-01-01]\n  workdays: ['2026-01-10']\n", encoding="utf-8"
    )

    calendar = loader.load_workdays(path)

    assert date(2026, 1, 1) in calendar.years[2026].holidays
    assert date(2026, 1, 10) in calendar.years[2026].workdays


def test_load_nds_missing_file_raises(tmp_path):
    with pytest.raises(ReferenceFileError):
        loader.load_nds(tmp_path / "missing.yaml")


def test_load_nds_empty_thresholds_raises(tmp_path):
    path = tmp_path / "nds.yaml"
    path.write_text(
        "law: 'Тестовый закон'\n"
        "law_url: 'https://example.invalid/law'\n"
        "fns_guide_url: 'https://example.invalid/guide'\n"
        "thresholds: []\n"
        "last_checked_at: '2026-01-01'\n",
        encoding="utf-8",
    )

    with pytest.raises(ReferenceFileError):
        loader.load_nds(path)


def test_load_nds_duplicate_income_year_raises(tmp_path):
    path = tmp_path / "nds.yaml"
    path.write_text(
        "law: 'Тестовый закон'\n"
        "law_url: 'https://example.invalid/law'\n"
        "fns_guide_url: 'https://example.invalid/guide'\n"
        "thresholds:\n"
        "  - {income_years: [2026], limit_rub: 20000000}\n"
        "  - {income_years: [2026], limit_rub: 15000000}\n"
        "last_checked_at: '2026-01-01'\n",
        encoding="utf-8",
    )

    with pytest.raises(ReferenceFileError):
        loader.load_nds(path)


def test_load_nds_missing_law_raises(tmp_path):
    path = tmp_path / "nds.yaml"
    path.write_text(
        "law_url: 'https://example.invalid/law'\n"
        "fns_guide_url: 'https://example.invalid/guide'\n"
        "thresholds:\n  - {income_years: [2026], limit_rub: 20000000}\n"
        "last_checked_at: '2026-01-01'\n",
        encoding="utf-8",
    )

    with pytest.raises(ReferenceFileError):
        loader.load_nds(path)


# --- get_reference: кеш ------------------------------------------------------------------------


def test_get_reference_caches_and_does_not_reload(monkeypatch):
    sentinel = Reference(
        catalog=Catalog(version="v", obligations=()),
        workdays=WorkdayCalendar(years={}),
        nds=NdsConfig(
            law="l",
            law_url="https://example.invalid/law",
            fns_guide_url="https://example.invalid/guide",
            thresholds=(),
            last_checked_at=date(2026, 1, 1),
        ),
    )
    calls = {"n": 0}

    def fake_load_reference(settings):
        calls["n"] += 1
        return sentinel

    monkeypatch.setattr(loader, "load_reference", fake_load_reference)
    loader.get_reference.cache_clear()
    try:
        first = loader.get_reference()
        second = loader.get_reference()

        assert first is sentinel
        assert second is sentinel
        assert calls["n"] == 1
    finally:
        loader.get_reference.cache_clear()
