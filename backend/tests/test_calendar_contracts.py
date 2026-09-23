"""T0 (#35): контракты app/calendar импортируются, тела — NotImplementedError до T2–T4.

Когда задача реализует свои функции, она убирает их из STUBS ниже.
"""

import dataclasses
import inspect
from datetime import date

import pytest

from app.calendar import build, dates, loader, reminders, types

STUBS = [
    loader.parse_date_rule,
    loader.parse_obligation,
    loader.load_catalog,
    loader.load_workdays,
    loader.load_nds,
    loader.load_reference,
    loader.get_reference,
    dates.is_workday,
    dates.next_workday,
    dates.apply_shift,
    dates.rule_dates,
    dates.obligation_dates,
    dates.nds_limit_for,
    dates.nds_payer,
    build.applies,
    build.build_horizon,
    build.build_calendar,
    reminders.send_at_utc,
    reminders.plan_obligation_notifications,
    reminders.plan_task_notification,
    reminders.cancel_pending,
]


def _dummy_args(fn) -> tuple[list, dict]:
    args, kwargs = [], {}
    for p in inspect.signature(fn).parameters.values():
        if p.default is not inspect.Parameter.empty:
            continue
        if p.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[p.name] = None
        else:
            args.append(None)
    return args, kwargs


@pytest.mark.parametrize("fn", STUBS, ids=lambda f: f"{f.__module__}.{f.__name__}")
async def test_stub_raises_not_implemented(fn):
    args, kwargs = _dummy_args(fn)
    with pytest.raises(NotImplementedError):
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            await result


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
