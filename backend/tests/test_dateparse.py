"""Тесты чистого парсера дат из свободного текста (экраны 9 и 10). Без БД, без сети."""

from datetime import date

from app.bot.dateparse import ParseStatus, parse_task_from_text

TODAY = date(2026, 9, 23)  # среда


# --- Экран 9, таблица форматов: каждый пример дословно ---------------------------------


def test_day_and_month_word_full() -> None:
    result = parse_task_from_text("оплатить аренду 5 ноября", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 11, 5)
    assert result.title == "Оплатить аренду"
    assert result.explicit_year is False


def test_day_and_month_word_abbreviated() -> None:
    result = parse_task_from_text("5 нояб", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 11, 5)
    assert result.title == ""  # кроме даты в сообщении ничего нет


def test_ddmm_without_year() -> None:
    result = parse_task_from_text("оплатить аренду 05.11", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 11, 5)
    assert result.explicit_year is False
    assert result.title == "Оплатить аренду"


def test_ddmm_with_year() -> None:
    result = parse_task_from_text("оплатить аренду 5.11.2026", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 11, 5)
    assert result.explicit_year is True
    assert result.title == "Оплатить аренду"


def test_relative_today() -> None:
    result = parse_task_from_text("позвонить клиенту сегодня", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == TODAY


def test_relative_tomorrow() -> None:
    result = parse_task_from_text("позвонить клиенту завтра", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 9, 24)


def test_relative_day_after_tomorrow() -> None:
    result = parse_task_from_text("позвонить клиенту послезавтра", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 9, 25)


def test_weekday_full_word() -> None:
    result = parse_task_from_text("сдать отчёт в понедельник", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 9, 28)  # ближайший будущий понедельник
    assert result.title == "Сдать отчёт"


def test_weekday_abbreviated() -> None:
    result = parse_task_from_text("сдать отчёт в пн", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 9, 28)


def test_weekday_today_is_not_counted() -> None:
    monday = date(2026, 9, 21)
    result = parse_task_from_text("сдать отчёт в понедельник", monday)
    assert result.due_date == date(2026, 9, 28)  # не сегодня, а через неделю


def test_weekday_with_vo_variant() -> None:
    result = parse_task_from_text("позвонить во вторник", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 9, 29)


def test_weekday_accusative_case() -> None:
    result = parse_task_from_text("сдать отчёт в среду", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2026, 9, 30)


# --- Переход через конец года -----------------------------------------------------------


def test_year_end_rollover_no_year() -> None:
    result = parse_task_from_text("оплатить взнос 5 января", date(2026, 12, 20))
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2027, 1, 5)


def test_year_end_rollover_ddmm() -> None:
    result = parse_task_from_text("оплатить взнос 05.01", date(2026, 12, 20))
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2027, 1, 5)


# --- Явный прошедший год — не роллится, past_date остаётся за обработчиком --------------


def test_explicit_past_year_kept_as_is() -> None:
    result = parse_task_from_text("оплатить аренду 5.11.2020", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.due_date == date(2020, 11, 5)
    assert result.explicit_year is True


# --- Несуществующие даты: похоже на дату, но она невозможна → NO_DATE, без исключений ----


def test_nonexistent_date_ddmm() -> None:
    result = parse_task_from_text("31.02", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.title == ""
    assert result.due_date is None


def test_nonexistent_day_of_month() -> None:
    result = parse_task_from_text("32 ноября", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.title == ""


def test_nonexistent_february_30() -> None:
    result = parse_task_from_text("30 февраля", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.title == ""


# --- Ложные срабатывания: не даты, хотя похожи на числа с точкой/пробелом ---------------


def test_false_positive_money_with_spaces() -> None:
    result = parse_task_from_text("оплатить 5 000 рублей", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None
    assert result.title == "Оплатить 5 000 рублей"


def test_false_positive_time_of_day() -> None:
    result = parse_task_from_text("встреча в 15:00", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None


def test_false_positive_quantity() -> None:
    result = parse_task_from_text("купить 3 пачки", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None
    assert result.title == "Купить 3 пачки"


def test_false_positive_recurring_not_supported() -> None:
    result = parse_task_from_text("платить каждый месяц 5 числа", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None


def test_false_positive_phone_number() -> None:
    result = parse_task_from_text("позвонить по номеру 89261234567", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None


def test_false_positive_decimal_amount_with_dot() -> None:
    # "1.5" синтаксически похоже на ДД.ММ (1 мая), но следом идёт маркер суммы.
    result = parse_task_from_text("выручка 1.5 млн", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None
    assert result.title == "Выручка 1.5 млн"


# --- title: обрезка до 60 символов, текст не теряется при нераспознанной дате -----------


def test_title_truncated_to_60_chars() -> None:
    long_text = "оплатить очень длинный список задолженностей по договору аренды офиса до 5 ноября"
    result = parse_task_from_text(long_text, TODAY)
    assert result.status is ParseStatus.PARSED
    assert len(result.title) <= 60
    assert result.title.startswith("Оплатить очень длинный")


def test_no_date_keeps_task_text() -> None:
    result = parse_task_from_text("подготовить документы для банка", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.title == "Подготовить документы для банка"
    assert result.due_date is None


def test_preposition_before_date_is_stripped() -> None:
    result = parse_task_from_text("сдать декларацию не позднее 5 ноября", TODAY)
    assert result.status is ParseStatus.PARSED
    assert result.title == "Сдать декларацию"


# --- «Не понял»: ни даты, ни похожего на задачу текста ----------------------------------


def test_unknown_single_word_no_verb() -> None:
    result = parse_task_from_text("привет", TODAY)
    assert result.status is ParseStatus.UNKNOWN
    assert result.title is None
    assert result.due_date is None


def test_unknown_empty_text() -> None:
    result = parse_task_from_text("", TODAY)
    assert result.status is ParseStatus.UNKNOWN


def test_unknown_whitespace_only() -> None:
    result = parse_task_from_text("   ", TODAY)
    assert result.status is ParseStatus.UNKNOWN


def test_no_date_single_word_verb_like() -> None:
    result = parse_task_from_text("заплатить", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.title == "Заплатить"
