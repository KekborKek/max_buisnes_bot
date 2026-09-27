"""Тесты чистого парсера дат из свободного текста (экраны 9 и 10). Без БД, без сети."""

from datetime import date, time

import pytest

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
    """Время — не дата: NO_DATE, но время не теряется и уходит из названия (#103)."""
    result = parse_task_from_text("встреча в 15:00", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert result.due_date is None
    assert result.title == "Встреча"
    assert result.remind_time == time(15, 0)


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


# --- #103: время вместе с датой ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "title", "due", "at"),
    [
        ("Оплатить аренду 5 ноября в 15:30", "Оплатить аренду", date(2026, 11, 5), time(15, 30)),
        ("оплатить аренду 5 ноября 15:30", "Оплатить аренду", date(2026, 11, 5), time(15, 30)),
        ("15:30 оплатить 5.11", "Оплатить", date(2026, 11, 5), time(15, 30)),
        ("позвонить завтра в 9:05", "Позвонить", date(2026, 9, 24), time(9, 5)),
        ("позвонить завтра в 0:30", "Позвонить", date(2026, 9, 24), time(0, 30)),
        ("бэкап завтра в 03:00", "Бэкап", date(2026, 9, 24), time(3, 0)),  # ведущий ноль
        ("позвонить в 3 завтра", "Позвонить", date(2026, 9, 24), time(15, 0)),  # 1–6 — днём
        ("позвонить завтра в 3", "Позвонить", date(2026, 9, 24), time(15, 0)),
        ("позвонить завтра в 3:30", "Позвонить", date(2026, 9, 24), time(15, 30)),
        ("позвонить завтра в 3 часа", "Позвонить", date(2026, 9, 24), time(15, 0)),
        ("в 15 завтра маме", "Маме", date(2026, 9, 24), time(15, 0)),  # голое «в N» перед датой
        ("встреча завтра в 15", "Встреча", date(2026, 9, 24), time(15, 0)),  # в конце текста
        ("встреча в 15, завтра", "Встреча", date(2026, 9, 24), time(15, 0)),  # перед запятой
        ("сменить пароль в 12 ночи завтра", "Сменить пароль", date(2026, 9, 24), time(0, 0)),
        ("бэкап в 2 ночи завтра", "Бэкап", date(2026, 9, 24), time(2, 0)),
        ("встреча с 15:00 до 16:00 завтра", "Встреча", date(2026, 9, 24), time(15, 0)),
        ("встреча 15:00–16:00 завтра", "Встреча", date(2026, 9, 24), time(15, 0)),
        ("записаться на 15:30 завтра", "Записаться", date(2026, 9, 24), time(15, 30)),
        ("встреча в пн в 15", "Встреча", date(2026, 9, 28), time(15, 0)),
        ("в 15 часов сдать отчёт 10.10", "Сдать отчёт", date(2026, 10, 10), time(15, 0)),
        ("позвонить в 15 ч завтра", "Позвонить", date(2026, 9, 24), time(15, 0)),
        ("позвонить в 15 ч. завтра", "Позвонить", date(2026, 9, 24), time(15, 0)),
        ("позвонить в 9 утра завтра", "Позвонить", date(2026, 9, 24), time(9, 0)),
        ("встреча в 3 дня 5 ноября", "Встреча", date(2026, 11, 5), time(15, 0)),
        ("обед в 12 дня завтра", "Обед", date(2026, 9, 24), time(12, 0)),
        ("ужин в 7 вечера завтра", "Ужин", date(2026, 9, 24), time(19, 0)),
        ("в 7:30 вечера ужин завтра", "Ужин", date(2026, 9, 24), time(19, 30)),
        ("созвон завтра в 23:59", "Созвон", date(2026, 9, 24), time(23, 59)),
    ],
)
def test_time_with_date(text, title, due, at) -> None:
    result = parse_task_from_text(text, TODAY)
    assert result.status is ParseStatus.PARSED
    assert (result.title, result.due_date, result.remind_time) == (title, due, at)


@pytest.mark.parametrize(
    ("text", "title", "due"),
    [
        ("оплатить 10.05", "Оплатить", date(2027, 5, 10)),  # точка — только дата
        ("оплатить в 15 ноября", "Оплатить", date(2026, 11, 15)),  # «в 15» + месяц — дата
        ("вырасти в 3 раза завтра", "Вырасти в 3 раза", date(2026, 9, 24)),
        ("оплатить в 5 числа завтра", "Оплатить в 5 числа", date(2026, 9, 24)),
        ("купить 3 пачки завтра", "Купить 3 пачки", date(2026, 9, 24)),
        ("позвать 15 человек завтра", "Позвать 15 человек", date(2026, 9, 24)),
        ("оплатить до 15 5 ноября", "Оплатить до 15", date(2026, 11, 5)),  # «до N» — срок
        ("сдать отчёт до 18:00 в пятницу", "Сдать отчёт до 18:00", date(2026, 9, 25)),
        ("Сдать отчёт к 9 утра завтра", "Сдать отчёт к 9 утра", date(2026, 9, 24)),
        ("сдать не позднее 15:00 завтра", "Сдать не позднее 15:00", date(2026, 9, 24)),
        ("Доставка в 2 этапа завтра", "Доставка в 2 этапа", date(2026, 9, 24)),
        ("Отчёт в 4 квартале 5 ноября", "Отчёт в 4 квартале", date(2026, 11, 5)),
        ("Прием в 11 кабинете завтра", "Прием в 11 кабинете", date(2026, 9, 24)),
        ("Забрать в 5 магазинах завтра", "Забрать в 5 магазинах", date(2026, 9, 24)),
        ("В 15 минут завтра позвонить", "В 15 минут позвонить", date(2026, 9, 24)),
        ("ждать 3 часа завтра", "Ждать 3 часа", date(2026, 9, 24)),  # длительность
        ("оплатить на 10:30 руб завтра", "Оплатить на 10:30 руб", date(2026, 9, 24)),
        ("встреча в 5 ночи завтра", "Встреча в 5 ночи", date(2026, 9, 24)),  # ночи — 12, 1–4
        ("встреча в 25:00 завтра", "Встреча в 25:00", date(2026, 9, 24)),
        ("встреча в 12:60 завтра", "Встреча в 12:60", date(2026, 9, 24)),
        ("ужин в 9 вечера завтра", "Ужин", date(2026, 9, 24)),  # 21:00 — это время
    ],
)
def test_not_a_time(text, title, due) -> None:
    result = parse_task_from_text(text, TODAY)
    assert result.status is ParseStatus.PARSED
    assert (result.title, result.due_date) == (title, due)
    if text.startswith("ужин"):
        assert result.remind_time == time(21, 0)
    else:
        assert result.remind_time is None


@pytest.mark.parametrize(
    ("text", "title", "at"),
    [
        ("оплатить 10:05", "Оплатить", time(10, 5)),  # двоеточие — время, не 10 мая
        ("позвонить бухгалтеру в 9 утра", "Позвонить бухгалтеру", time(9, 0)),
        ("встреча в 15", "Встреча", time(15, 0)),
    ],
)
def test_time_without_date_is_no_date_and_keeps_time(text, title, at) -> None:
    result = parse_task_from_text(text, TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert (result.title, result.due_date, result.remind_time) == (title, None, at)


@pytest.mark.parametrize(
    "text", ["выручка 1.5 млн", "созвон в 12.30", "выручка в 1,5 раза больше", "купить 3 пачки"]
)
def test_numbers_are_neither_date_nor_time(text) -> None:
    result = parse_task_from_text(text, TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert (result.due_date, result.remind_time) == (None, None)
    assert result.title == text[0].upper() + text[1:]


def test_only_time_is_unknown() -> None:
    assert parse_task_from_text("15:30", TODAY).status is ParseStatus.UNKNOWN
    assert parse_task_from_text("в 9 утра", TODAY).status is ParseStatus.UNKNOWN


def test_invalid_date_keeps_time() -> None:
    result = parse_task_from_text("оплатить 31.02 в 10:00", TODAY)
    assert result.status is ParseStatus.NO_DATE
    assert (result.title, result.remind_time) == ("Оплатить", time(10, 0))


def test_date_without_time_has_no_time() -> None:
    assert parse_task_from_text("оплатить аренду 5 ноября", TODAY).remind_time is None
