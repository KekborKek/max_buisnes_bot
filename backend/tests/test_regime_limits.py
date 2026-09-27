"""Лимит режима на экране 3 (#107): справочник, проверка, предупреждение и «Что делать».

Лимиты — из фикстуры backend/tests/fixtures/regime_limits.yaml (ТЕСТОВЫЕ ДАННЫЕ, подменяет
conftest.fixture_regime_limits): патент 20 млн ₽ за 2025, 15 млн ₽ за 2026, 10 млн ₽ с 2027;
АУСН 60 млн ₽ за 2024–2026. «Сейчас» — 29.09.2026, доход в онбординге — за 2025 год.
"""

import itertools
import logging
from pathlib import Path

import pytest

from app.calendar import loader
from app.calendar.regime_limits import check, limit_for
from app.calendar.types import INCOME_BANDS, REGIMES, ReferenceFileError
from app.core.texts import t
from tests.test_nds_answer import clock, events, onboard, payloads, press, run  # noqa: F401

pytestmark = pytest.mark.usefixtures("fixture_reference")

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "regime_limits.yaml"
HOWTO = "reglim:howto:"

VALID = """
last_checked_at: "2026-01-01"
regimes:
  patent:
    norm: "н"
    norm_url: "https://example.invalid/l"
    fns_url: "https://example.invalid/f"
    limits:
      - { income_years: [2025], limit_rub: 20000000, verified: true }
      - { income_years_from: 2026, limit_rub: 10000000, verified: false }
    steps:
      - { text: "1", norm: "н", verified: true }
      - { text: "2", norm: "н", verified: true }
      - { text: "3", norm: "н", verified: true }
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "regime_limits.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- справочник --------------------------------------------------------------------------------


def test_fixture_loads():
    config = loader.load_regime_limits(FIXTURE)
    assert set(config.regimes) == {"patent", "ausn"}
    patent = config.regimes["patent"]
    assert [limit_for(patent, y) for y in (2024, 2025, 2026, 2027, 2040)] == [
        None,
        20_000_000,
        15_000_000,
        10_000_000,
        10_000_000,
    ]
    assert len(patent.steps) == 3
    assert patent.steps[2].verified is False


def test_real_file_loads_with_sources_and_checked_facts():
    """content/regime_limits.yaml: оба режима, у каждого факта ссылка и отметка сверки."""
    config = loader.load_regime_limits(ROOT / "content" / "regime_limits.yaml")
    assert set(config.regimes) == {"patent", "ausn"}
    for rules in config.regimes.values():
        assert rules.norm_url.startswith("https://")
        assert rules.fns_url.startswith("https://")
        assert 3 <= len(rules.steps) <= 5
        assert all(step.norm for step in rules.steps)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('last_checked_at: "2026-01-01"', "last_checked_at: вчера"),
        ("  patent:", "  usn7:"),
        ("norm_url: ", "norm_link: "),
        ("limit_rub: 20000000", "limit_rub: '20'"),
        ("income_years: [2025]", "income_years: []"),
        ("income_years_from: 2026", "income_years: [2025]"),  # год в двух лимитах
        ("{ income_years: [2025],", "{ income_years_from: 2020,"),  # два income_years_from
        ("limit_rub: 10000000, verified: false", "limit_rub: 10000000"),  # нет verified
        ('      - { text: "3", norm: "н", verified: true }\n', ""),  # два шага
        ('text: "1"', 'text: ""'),
    ],
)
def test_broken_format_is_file_error(tmp_path, old, new):
    assert old in VALID
    with pytest.raises(ReferenceFileError):
        loader.load_regime_limits(write(tmp_path, VALID.replace(old, new, 1)))


def test_valid_sample_loads(tmp_path):
    assert loader.load_regime_limits(write(tmp_path, VALID)).regimes["patent"].norm == "н"


def test_unparsable_file_is_file_error(tmp_path):
    with pytest.raises(ReferenceFileError):
        loader.load_regime_limits(write(tmp_path, "regimes: [\n"))
    with pytest.raises(ReferenceFileError):
        loader.load_regime_limits(tmp_path / "missing.yaml")


# --- проверка: все пары режим × доход ------------------------------------------------------------

EXPECTED_2025 = {
    ("patent", "lt10"): "ok",
    ("patent", "10_20"): "ok",
    ("patent", "20_60"): "exceeded",
    ("patent", "gt60"): "exceeded",
    ("ausn", "lt10"): "ok",
    ("ausn", "10_20"): "ok",
    ("ausn", "20_60"): "ok",
    ("ausn", "gt60"): "exceeded",
}


@pytest.mark.parametrize(
    ("regime", "income"), list(itertools.product(sorted(REGIMES), sorted(INCOME_BANDS)))
)
def test_check_every_pair_for_2025(regime, income, fixture_regime_limits):
    expected = EXPECTED_2025.get((regime, income), "unknown")  # usn*, «Не знаю» — не судим
    assert check(regime, income, 2025, fixture_regime_limits) == expected


def test_check_band_crossing_limit_or_no_limit_is_unknown(fixture_regime_limits):
    # 10–20 млн при лимите 15 млн — нельзя сказать, превышен ли
    assert check("patent", "10_20", 2026, fixture_regime_limits) == "unknown"
    assert check("patent", "10_20", 2027, fixture_regime_limits) == "exceeded"
    # на 2027 год лимита АУСН в справочнике нет
    assert check("ausn", "gt60", 2027, fixture_regime_limits) == "unknown"
    assert check(None, None, 2025, fixture_regime_limits) == "unknown"


# --- бот, экран 3 --------------------------------------------------------------------------------


def warning_text(income: str, regime: str, limit: str) -> str:
    income_label = t(f"onboarding.q1_{income}")
    return t(
        "regime_limit.warning",
        income_year=2025,
        income=income_label[0].lower() + income_label[1:],
        regime_name=t(f"onboarding.q2_{regime}"),
        limit=t("start.nds_limit", value=limit),
    )


@pytest.mark.parametrize(
    ("income", "regime", "limit"),
    [("20_60", "patent", "20"), ("gt60", "patent", "20"), ("gt60", "ausn", "60")],
)
async def test_exceeded_limit_sends_warning_after_nds_answer(fake_max, income, regime, limit):
    screen3 = await onboard(fake_max, income, regime)

    assert screen3["text"].startswith(
        t("nds.special_regime", regime_name=t(f"onboarding.q2_{regime}"))
    )
    warning = fake_max.sent[-1]
    assert warning is not screen3
    assert warning["text"] == warning_text(income, regime, limit)
    assert payloads(warning) == [[HOWTO + regime]]
    assert await events("regime_limit_warned") == [{"regime": regime, "income_band": income}]


@pytest.mark.parametrize(
    ("income", "regime"),
    [
        ("20_60", "ausn"),
        ("10_20", "patent"),
        ("lt10", "patent"),
        ("gt60", "usn6"),
        ("20_60", "usn15"),
        ("unknown", "patent"),
        ("unknown", "ausn"),
        ("gt60", "unknown"),  # режим «Не знаю» → УСН 6%
    ],
)
async def test_no_warning_when_not_exceeded(fake_max, income, regime):
    screen3 = await onboard(fake_max, income, regime)

    assert fake_max.sent[-1] is screen3
    assert all(not m["text"].startswith("Вы указали доход") for m in fake_max.sent)
    assert await events("regime_limit_warned") == []


async def test_howto_button_shows_steps_source_and_links(fake_max, fixture_regime_limits):
    await onboard(fake_max, "gt60", "patent")

    msg = await run(fake_max, press(HOWTO + "patent"))

    rules = fixture_regime_limits.regimes["patent"]
    for number, step in enumerate(rules.steps, start=1):
        assert f"{number}. {step.text}" in msg["text"]
    assert rules.norm in msg["text"]
    assert "01.01.2026" in msg["text"]
    assert t("regime_limit.not_advice") in msg["text"]
    buttons = msg["attachments"][0]["payload"]["buttons"]
    assert buttons == [
        [
            {"type": "link", "text": t("regime_limit.btn_law"), "url": rules.norm_url},
            {"type": "link", "text": t("regime_limit.btn_fns"), "url": rules.fns_url},
        ]
    ]
    assert await events("regime_limit_howto_opened") == [{"regime": "patent"}]


async def test_howto_for_unknown_regime_is_error_with_retry(fake_max):
    await onboard(fake_max, "lt10", "usn6")

    msg = await run(fake_max, press(HOWTO + "usn6"))

    assert msg["text"] == t("common.error")
    assert payloads(msg) == [[HOWTO + "usn6"]]
    assert await events("regime_limit_howto_opened") == []


async def test_broken_regime_limits_file_does_not_break_screen_3(
    fake_max, monkeypatch, tmp_path, caplog
):
    broken = write(tmp_path, "regimes: [\n")
    monkeypatch.setattr(loader, "get_regime_limits", lambda: loader.load_regime_limits(broken))

    with caplog.at_level(logging.ERROR):
        screen3 = await onboard(fake_max, "gt60", "patent")

    assert screen3["text"].startswith(t("nds.special_regime", regime_name="Патент"))
    assert fake_max.sent[-1] is screen3
    assert payloads(screen3) == [["calendar:build", "nds:why"]]
    assert "regime_limits.yaml" in caplog.text
    assert await events("regime_limit_warned") == []


def test_buttons_fit_20_chars():
    for key in ("btn_howto", "btn_law", "btn_fns"):
        assert len(t(f"regime_limit.{key}")) <= 20
