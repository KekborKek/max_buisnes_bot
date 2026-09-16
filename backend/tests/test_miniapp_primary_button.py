"""Баг №3 (issue #5): кнопка «Начать» в мини-аппе не даёт никакой реакции.

Причина: в `miniapp/src/App.tsx` обработчик `onClick` кнопки состоял из одного
вызова `api.track("primary_action_clicked")` — запрос уходил и возвращал 204,
но интерфейс никак на это не реагировал (ни индикации, ни перехода), а падение
запроса (например, протухший initData → 401) улетало необработанным промисом.

Тестового раннера для фронта в проекте нет (vitest/@testing-library не
подключены — зависимости добавляет только техлид), поэтому баг фиксируется
статическими проверками по исходнику `miniapp/src/App.tsx`.
"""

import re
from pathlib import Path

APP_TSX = Path(__file__).resolve().parents[2] / "miniapp" / "src" / "App.tsx"

# Ищем кнопку с текстом texts.primaryAction и вытаскиваем имя/тело её onClick.
PRIMARY_ACTION_TEXT = re.compile(r"\{texts\.primaryAction\}")
ONCLICK_ATTR = re.compile(r"onClick=\{([^}]*)\}")


def _source() -> str:
    return APP_TSX.read_text(encoding="utf-8")


def _button_block(source: str) -> str:
    text_match = PRIMARY_ACTION_TEXT.search(source)
    assert text_match, "не найдена кнопка с texts.primaryAction в miniapp/src/App.tsx"
    # Ближайший <Button перед текстом — начало открывающего тега этой кнопки.
    start = source.rindex("<Button", 0, text_match.start())
    return source[start : text_match.end()]


def _extract_braced_body(source: str, start_of_brace: int) -> str:
    """Возвращает содержимое {...} с балансировкой скобок начиная с indexа '{'."""
    depth = 0
    for i in range(start_of_brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start_of_brace : i + 1]
    raise AssertionError("не удалось найти конец блока — несбалансированные скобки")


def _onclick_handler_body(source: str) -> str:
    block = _button_block(source)
    onclick = ONCLICK_ATTR.search(block)
    assert onclick, "у кнопки texts.primaryAction нет onClick"
    target = onclick.group(1).strip()

    # onClick={() => ...} — инлайн-функция.
    if target.startswith("(") or target.startswith("async"):
        brace_idx = block.index("{", onclick.start())
        return _extract_braced_body(block, brace_idx)

    # onClick={handleSomething} — ссылка на функцию, определённую выше в файле
    # (обычно через useState-сеттеры внутри useCallback(async () => { ... })).
    name = target
    def_match = re.search(rf"\bconst\s+{re.escape(name)}\s*=", source)
    assert def_match, f"не найдено определение обработчика '{name}'"
    arrow_match = re.search(r"=>\s*\{", source[def_match.end() :])
    assert arrow_match, f"обработчик '{name}' должен быть функцией с телом в {{...}}"
    brace_idx = def_match.end() + arrow_match.end() - 1
    return _extract_braced_body(source, brace_idx)


def test_primary_button_gives_user_feedback():
    """Обработчик не должен сводиться к одному api.track(...) — нужна видимая реакция UI."""
    source = _source()
    handler = _onclick_handler_body(source)

    calls = re.findall(r"\bapi\.track\(", handler)
    assert calls, "обработчик кнопки должен по-прежнему отправлять аналитику через api.track"

    # Видимая реакция — это изменение локального состояния компонента (useState-сеттер)
    # или явный переход/навигация. Проверяем, что в обработчике есть что-то, кроме трекинга.
    has_state_change = re.search(r"\bset[A-Z]\w*\(", handler)
    assert has_state_change, (
        "обработчик кнопки texts.primaryAction состоит из одного api.track(...) — "
        "после нажатия пользователь не видит никакой реакции интерфейса "
        f"(тело обработчика: {handler!r})"
    )


def test_primary_button_handles_failed_request():
    """Ошибка запроса не должна улетать необработанным промисом — нужен catch/try."""
    source = _source()
    handler = _onclick_handler_body(source)

    has_catch = ".catch(" in handler
    has_try = re.search(r"\btry\s*\{", handler) and re.search(r"\bcatch\b", handler)
    assert has_catch or has_try, (
        "обработчик кнопки texts.primaryAction не обрабатывает ошибку запроса — "
        "при сбое (например, 401 из-за протухшего initData) пользователь получит "
        "только unhandled rejection в консоли и полную тишину в интерфейсе "
        f"(тело обработчика: {handler!r})"
    )
