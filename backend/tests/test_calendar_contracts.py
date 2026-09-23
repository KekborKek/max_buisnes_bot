"""T0 (#35): контракты app/calendar — неизменяемые датаклассы, словари и исключения.

Тест заглушек NotImplementedError убран: T2–T4 реализовали все функции контракта.
"""

import dataclasses
from datetime import date

import pytest

from app.calendar import types


def _sample_obligation() -> types.Obligation:
    """ТЕСТОВЫЕ ДАННЫЕ: выдуманная запись, не налоговый срок."""
    return types.Obligation(
        id="test_quarterly",
        title="Тестовое обязательство",
        category="reports",
        date_rule=types.QuarterlyRule(offset_month=1, day=15),
        shift="next_workday",
        applies_if=types.AppliesIf(regime=frozenset({"usn6"})),
        needs_prep=False,
        norm="Тестовая норма",
        source_url="https://example.invalid/norm",
        howto_steps=("Шаг 1", "Шаг 2", "Шаг 3"),
        howto_link=types.HowtoLink(label="Открыть", url="https://example.invalid"),
        penalty_text="Тестовый текст",
        rule_version=1,
        last_checked_at=date(2030, 1, 1),
    )


def test_records_are_immutable():
    ob = _sample_obligation()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ob.title = "другое"  # type: ignore[misc]
    assert ob.date_rule.type == "quarterly"


def test_date_rule_union_covers_three_types():
    rules = [
        types.YearlyRule(month=1, day=1),
        types.QuarterlyRule(offset_month=1, day=1),
        types.MonthlyInQuarterRule(months=(1, 2, 3), day=1),
    ]
    assert [r.type for r in rules] == ["yearly", "quarterly", "monthly_in_quarter"]
    assert all(isinstance(r, types.DateRule) for r in rules)


def test_income_band_bounds_are_contiguous():
    """Границы кнопок онбординга без дыр и пересечений; unknown границ не имеет."""
    bounds = types.INCOME_BAND_BOUNDS_RUB
    assert set(bounds) == types.INCOME_BANDS - {"unknown"}
    ordered = sorted(bounds.values(), key=lambda b: b[0])
    assert ordered[0][0] == 0 and ordered[-1][1] is None
    for (_, upper), (lower, _) in zip(ordered, ordered[1:], strict=False):
        assert upper == lower


def test_errors_carry_context():
    err = types.InvalidRecordError("test_quarterly", "нет поля norm")
    assert err.record_id == "test_quarterly" and "norm" in str(err)
    assert isinstance(err, types.CalendarError)
    assert types.MissingYearError(2031).year == 2031
