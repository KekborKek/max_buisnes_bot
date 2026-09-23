"""Форматирование дат и чисел для текстов бота (product.md, «Форма»; экран 6).

Слова, которые видит пользователь, — в content/texts.yaml (раздел `reminder`). Здесь только
данные языка: названия месяцев в родительном падеже и правило выбора формы слова после числа.
Модулем пользуются напоминания (экран 6) и следующие экраны бота.
"""

from collections.abc import Sequence
from datetime import date

from app.core.texts import t

# Родительный падеж: «28 октября». Данные языка, а не тексты продукта.
MONTHS_GENITIVE: tuple[str, ...] = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

# «Через месяц» — ровно для d30: напоминание за 30 дней (reminders.md).
_MONTH_DAYS = 30


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова после числа: 1 день, 2 дня, 5 дней, 11 дней, 21 день."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_date(day: date, today: date) -> str:
    """«28 октября»; год — только если он не текущий: «26 апреля 2027»."""
    text = f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"
    if day.year != today.year:
        text += f" {day.year}"
    return text


def date_with_shift(due_date: date, original_date: date | None, today: date) -> str:
    """Дата срока и сразу после неё `reminder.shift_note`, если срок перенесён с выходного."""
    text = format_date(due_date, today)
    if original_date is not None and original_date != due_date:
        text += t("reminder.shift_note", original_date=format_date(original_date, today))
    return text


def when_words(due_date: date, today: date) -> str:
    """`{when}`: «Сегодня» / «Завтра» / «Через месяц» / «Через N дней».

    Прошедший срок сюда не попадает (overdue не группируется, D30); если всё же попал —
    «Сегодня», чтобы не писать «Через -1 день».
    """
    days = (due_date - today).days
    if days <= 0:
        return t("reminder.when_today")
    if days == 1:
        return t("reminder.when_tomorrow")
    if days == _MONTH_DAYS:
        return t("reminder.when_month")
    unit = plural(days, t("reminder.day_one"), t("reminder.day_few"), t("reminder.day_many"))
    return t("reminder.when_days", n=days, unit=unit)


def count_words(n: int) -> str:
    """`{count_words}`: «два срока», «три срока», «четыре срока», дальше «5 сроков»."""
    if 2 <= n <= 4:
        return t(f"reminder.count_{n}")
    unit = plural(n, t("reminder.term_one"), t("reminder.term_few"), t("reminder.term_many"))
    return t("reminder.count_n", n=n, unit=unit)


def join_titles(titles: Sequence[str]) -> str:
    """`{titles}`: «А», «А и Б», «А, Б и В»."""
    if len(titles) <= 1:
        return "".join(titles)
    head = t("reminder.titles_sep").join(titles[:-1])
    return f"{head}{t('reminder.titles_last_sep')}{titles[-1]}"
