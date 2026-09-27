"""T5b (#53): экран 3 бота — ответ про НДС (docs/screens/03-nds-answer.md, D14).

Справочники — фикстуры backend/tests/fixtures (порог 20 млн для доходов 2025–2026,
15 млн для 2027). НДС-записей в фикстурах нет — для плательщика их добавляет `with_nds`
(ТЕСТОВЫЕ ДАННЫЕ, собраны прямо здесь). «Сейчас» заморожено: 29.09.2026 — платежи по НДС
за II квартал уже прошли, ближайшее — декларация за III квартал 25.10.2026 (воскресенье →
26 октября).
"""

import itertools
import logging
import re
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.bot.dispatcher import process_update
from app.bot.handlers import calendar_ready, common, nds_answer
from app.calendar import loader
from app.calendar.types import (
    AppliesIf,
    HowtoLink,
    MonthlyInQuarterRule,
    NdsConfig,
    NdsThreshold,
    Obligation,
    QuarterlyRule,
    Reference,
)
from app.core.db import SessionLocal
from app.core.models import Event, Profile
from app.core.texts import t
from tests.conftest import load_update

pytestmark = pytest.mark.usefixtures("fixture_reference")

USER_ID = 42
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)

_seq = itertools.count(1)


# --- помощники (кроме clock их берёт и test_calendar_ready) ----------------------------


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Подменяемое «сейчас»: clock["now"] можно сдвигать внутри теста."""
    state = {"now": NOW}
    monkeypatch.setattr(common, "now", lambda: state["now"])
    return state


def press(payload: str) -> dict:
    n = next(_seq)
    update = load_update("callback_start_check")
    update["timestamp"] += n
    update["callback"] = dict(update["callback"], payload=payload, callback_id=f"cb-t5b-{n}")
    return update


def started() -> dict:
    update = load_update("bot_started")
    update["timestamp"] += next(_seq)
    return update


def payloads(message: dict) -> list[list[str | None]]:
    rows = message["attachments"][0]["payload"]["buttons"]
    return [[b.get("payload") for b in row] for row in rows]


async def run(fake_max, *updates: dict) -> dict:
    for update in updates:
        await process_update(update, fake_max)
    return fake_max.sent[-1]


async def onboard(
    fake_max, income: str = "lt10", regime: str = "usn6", employees: str = "no", tz: str = ""
) -> dict:
    """/start → «Проверить НДС» → четыре ответа; возвращает экран 3.

    Экран 3 — первое сообщение на последний ответ: за ним может прийти предупреждение
    о лимите режима (#107).
    """
    tz = tz or "Europe/Moscow"
    await run(
        fake_max,
        started(),
        press("start:check"),
        press(f"onb:1:{income}"),
        press(f"onb:2:{regime}"),
        press(f"onb:3:{employees}"),
    )
    before = len(fake_max.sent)
    await run(fake_max, press(f"onb:4:{tz}"))
    return fake_max.sent[before]


async def events(name: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.scalars(select(Event.props).where(Event.name == name).order_by(Event.id))
        return list(rows)


async def get_profile() -> Profile | None:
    async with SessionLocal() as s:
        return await s.get(Profile, USER_ID)


def _nds_obligation(ob_id: str, title: str, rule) -> Obligation:
    return Obligation(
        id=ob_id,
        title=title,
        category="taxes",
        date_rule=rule,
        shift="next_workday",
        applies_if=AppliesIf(nds_payer=True),
        needs_prep=True,
        norm="Тестовая норма НДС",
        source_url="https://example.invalid/nds",
        howto_steps=("Тестовый шаг 1.", "Тестовый шаг 2.", "Тестовый шаг 3."),
        howto_link=HowtoLink(label="Открыть тест", url="https://example.invalid/nds-howto"),
        penalty_text="Тестовый текст о пене.",
        rule_version=1,
        last_checked_at=date(2026, 1, 1),
    )


# ТЕСТОВЫЕ ДАННЫЕ: блок НДС — декларация до 25-го и платежи до 28-го после квартала.
NDS_OBLIGATIONS = (
    _nds_obligation(
        "test_nds_declaration",
        "Тестовая декларация по НДС",
        QuarterlyRule(offset_month=1, day=25),
    ),
    _nds_obligation(
        "test_nds_payment", "Тестовый платёж по НДС", MonthlyInQuarterRule(months=(1, 2, 3), day=28)
    ),
)


@pytest.fixture
def with_nds(monkeypatch, fixture_reference) -> Reference:
    """Фикстурный справочник + НДС-записи."""
    catalog = replace(
        fixture_reference.catalog,
        obligations=fixture_reference.catalog.obligations + NDS_OBLIGATIONS,
    )
    reference = replace(fixture_reference, catalog=catalog)
    monkeypatch.setattr(loader, "get_reference", lambda: reference)
    return reference


def limit(value: str = "20") -> str:
    return t("start.nds_limit", value=value)


NOT_PAYER = t("nds.not_payer", limit=limit(), years="2025–2026") + " " + t("disclaimer.profile")
PAYER = t(
    "nds.payer",
    limit=limit(),
    next_title="тестовая декларация по НДС",
    next_date="26 октября",
    shift_note=t("nds.shift_note", original_date="25 октября"),
)
UNKNOWN = t("nds.unknown", limit=limit())


# --- варианты текста (D14) ------------------------------------------------------------------


@pytest.mark.usefixtures("with_nds")
@pytest.mark.parametrize(
    ("income", "regime", "expected"),
    [
        ("lt10", "usn6", NOT_PAYER),
        ("10_20", "usn15", NOT_PAYER),  # тот же текст, что и до 10 млн
        ("20_60", "usn6", PAYER),
        ("gt60", "usn15", PAYER),  # D18: как 20–60
        ("unknown", "usn6", UNKNOWN),
        # особый режим важнее дохода; дисклеймер — только при nds_payer = false
        ("20_60", "patent", t("nds.special_regime", regime_name="Патент")),
        (
            "lt10",
            "ausn",
            t("nds.special_regime", regime_name="АУСН") + " " + t("disclaimer.profile"),
        ),
    ],
)
async def test_each_profile_gets_its_variant(fake_max, income, regime, expected):
    msg = await onboard(fake_max, income, regime)

    assert msg["text"] == expected
    assert payloads(msg) == [["calendar:build", "nds:why"]]


@pytest.mark.usefixtures("with_nds")
async def test_four_variants_are_different(fake_max):
    texts = set()
    for income, regime in (
        ("lt10", "usn6"),
        ("20_60", "usn6"),
        ("unknown", "usn6"),
        ("20_60", "patent"),
    ):
        texts.add((await onboard(fake_max, income, regime))["text"])
    assert len(texts) == 4


@pytest.mark.usefixtures("with_nds")
async def test_unknown_regime_adds_second_paragraph(fake_max):
    msg = await onboard(fake_max, "lt10", "unknown")

    assert msg["text"] == NOT_PAYER + "\n\n" + t("nds.regime_guessed")


@pytest.mark.usefixtures("with_nds")
@pytest.mark.parametrize(("income", "regime"), [("lt10", "usn6"), ("20_60", "usn6")])
async def test_no_tax_amount_and_no_promise_of_lower_threshold(fake_max, income, regime):
    text = (await onboard(fake_max, income, regime))["text"]

    assert not re.search(r"\d\s*₽", text)  # сумм нет: единственное число с ₽ — порог «20 млн ₽»
    assert not re.search(r"\b202[78]\b", text)  # «порог снизится в 2027/2028» — нигде


@pytest.mark.usefixtures("with_nds")
async def test_payer_date_without_shift_has_no_note(fake_max, clock):
    clock["now"] = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)  # декларация уже прошла
    msg = await onboard(fake_max, "20_60", "usn6")

    assert "до 28 октября." in msg["text"]
    assert "выходной" not in msg["text"]
    assert "тестовый платёж по НДС" in msg["text"]


async def test_limit_and_years_come_from_nds_yaml(fake_max, fixture_reference, monkeypatch):
    nds = replace(
        fixture_reference.nds,
        thresholds=(NdsThreshold(income_years=(2024, 2025, 2026), limit_rub=30_000_000),),
    )
    monkeypatch.setattr(loader, "get_reference", lambda: replace(fixture_reference, nds=nds))

    msg = await onboard(fake_max, "lt10", "usn6")

    assert msg["text"].startswith(t("nds.not_payer", limit=limit("30"), years="2024–2026"))


async def test_nds_result_shown_event(fake_max, with_nds):
    await onboard(fake_max, "20_60", "unknown")
    await onboard(fake_max, "unknown", "usn6")

    assert await events("nds_result_shown") == [
        {"income_band": "20_60", "regime": "usn6", "nds_payer": True},
        {"income_band": "unknown", "regime": "usn6", "nds_payer": None},
    ]


# --- «Почему так?» ------------------------------------------------------------------------


async def test_why_shows_thresholds_law_and_links(fake_max, fixture_reference):
    await onboard(fake_max)
    msg = await run(fake_max, press("nds:why"))

    thresholds = f"{limit('20')} — доходы за 2025–2026 годы, {limit('15')} — за 2027-й"
    assert msg["text"] == t("nds.why", thresholds=thresholds, law=fixture_reference.nds.law)
    rows = msg["attachments"][0]["payload"]["buttons"]
    assert rows[0] == [
        {"type": "link", "text": "Закон", "url": fixture_reference.nds.law_url},
        {"type": "link", "text": "Разъяснения ФНС", "url": fixture_reference.nds.fns_guide_url},
    ]
    assert payloads(msg)[1] == ["calendar:build"]
    assert await events("nds_why_opened") == [{}]


def test_thresholds_text_matches_spec_example():
    nds = NdsConfig(
        law="—",
        law_url="https://example.invalid",
        fns_guide_url="https://example.invalid",
        thresholds=(
            NdsThreshold(income_years=(2029,), limit_rub=15_000_000),
            NdsThreshold(income_years=(2025, 2026, 2027, 2028), limit_rub=20_000_000),
            NdsThreshold(income_years=(2030,), limit_rub=10_000_000),
        ),
        last_checked_at=date(2026, 1, 1),
    )
    expected = "20 млн ₽ — доходы за 2025–2028 годы, 15 млн ₽ — за 2029-й, 10 млн ₽ — за 2030-й"
    assert nds_answer.thresholds_text(nds).replace(" ", " ") == expected


# --- ошибки -----------------------------------------------------------------------------------


async def test_payer_without_nds_records_is_data_error_then_retry(
    fake_max, fixture_reference, monkeypatch, caplog
):
    caplog.set_level(logging.ERROR)
    msg = await onboard(fake_max, "20_60", "unknown")  # в фикстурах НДС-записей нет

    assert msg["text"] == t("common.error")
    assert payloads(msg) == [["nds:show"]]
    assert await events("error") == [{"where": "nds_answer", "kind": "no_nds_obligations"}]
    assert "applies_if.nds_payer: true" in caplog.text
    assert await events("nds_result_shown") == []
    assert (await get_profile()).income_band == "20_60"  # ответы не потеряны

    catalog = replace(
        fixture_reference.catalog,
        obligations=fixture_reference.catalog.obligations + NDS_OBLIGATIONS,
    )
    reference = replace(fixture_reference, catalog=catalog)
    monkeypatch.setattr(loader, "get_reference", lambda: reference)
    msg = await run(fake_max, press("nds:show"))

    assert msg["text"] == PAYER + "\n\n" + t("nds.regime_guessed")  # флаг «Не знаю» сохранился
    assert len(await events("nds_result_shown")) == 1
    assert len(await events("onboarding_completed")) == 1


async def test_no_threshold_for_income_year_is_error(fake_max, clock):
    clock["now"] = datetime(2029, 3, 1, 9, 0, tzinfo=UTC)  # доход за 2028 — в фикстуре порога нет
    msg = await onboard(fake_max, "lt10", "usn6")

    assert msg["text"] == t("common.error")
    assert payloads(msg) == [["nds:show"]]
    # первая ошибка — на приветствии (экран 1 тоже без порога), последняя — экран 3
    assert (await events("error"))[-1] == {"where": "nds_answer", "kind": "reference_missing"}


async def test_old_show_button_with_incomplete_profile_asks_question(fake_max):
    await onboard(fake_max)
    msg = await run(fake_max, press("start:check"), press("nds:show"))

    assert msg["text"] == t("onboarding.q1", income_year=2025)


# --- форма кнопок -------------------------------------------------------------------------


def test_screen_3_buttons_fit_20_chars_and_two_per_row(fixture_reference):
    boards = [nds_answer.answer_keyboard(), nds_answer.why_keyboard(fixture_reference.nds)]
    for board in boards:
        for row in board["payload"]["buttons"]:
            assert 1 <= len(row) <= 2
            for button in row:
                assert len(button["text"]) <= 20, button["text"]
                assert not button["text"].startswith("["), button["text"]


def test_lower_first_keeps_abbreviation():
    assert calendar_ready.lower_first("Декларация по НДС") == "декларация по НДС"
    assert calendar_ready.lower_first("НДС: платёж") == "НДС: платёж"
