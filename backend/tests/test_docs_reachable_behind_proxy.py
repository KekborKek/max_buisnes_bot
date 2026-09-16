"""Issue #7: панель с API (`/docs`) и `/openapi.json` недоступны через прокси.

Мини-апп и бэкенд живут на одном домене, но наружу смотрит прокси:
локально это nginx из `miniapp/nginx.conf` (порт 8080), в проде — Caddy
из `deploy/Caddyfile`. Оба проксируют на бэкенд только `/api/` (и `/webhook/`,
`/health` — в Caddy), а всё остальное отдают мини-аппу. В nginx это ещё и
SPA-фолбэк `try_files $uri /index.html`, поэтому `http://localhost:8080/docs`
отвечает 200 с HTML мини-аппа — ошибка ничем не видна, просто «панель пропала».

Поднимать Docker в pytest нельзя, поэтому инвариант проверяется статически
по самим конфигам: маршруты бэкенда должны быть объявлены в прокси раньше,
чем общий фолбэк на мини-апп.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = ROOT / "miniapp" / "nginx.conf"
CADDYFILE = ROOT / "deploy" / "Caddyfile"

BACKEND_UPSTREAM = "backend:8000"

# location [= | ~ | ~* | ^~] /путь {   — модификатор необязателен
NGINX_LOCATION = re.compile(r"^\s*location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{", re.MULTILINE)
# handle /путь* {  и handle_path — в Caddy директива может идти без пути (фолбэк)
CADDY_HANDLE = re.compile(r"^\s*handle(?:_path)?(?:\s+(\S+))?\s*\{", re.MULTILINE)


def _read(path: Path) -> str:
    assert path.exists(), f"нет файла конфигурации прокси: {path}"
    return path.read_text(encoding="utf-8")


def _block_at(source: str, open_brace: int) -> str:
    """Тело блока {...} с балансировкой скобок, начиная с индекса '{'."""
    depth = 0
    for i in range(open_brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : i + 1]
    raise AssertionError(f"несбалансированные скобки в конфигурации начиная с {open_brace}")


def _nginx_locations() -> list[tuple[str, str, str]]:
    """[(модификатор, путь, тело блока)] в порядке объявления."""
    source = _read(NGINX_CONF)
    return [
        (m.group(1) or "", m.group(2), _block_at(source, source.index("{", m.start())))
        for m in NGINX_LOCATION.finditer(source)
    ]


def _caddy_handles() -> list[tuple[str, str]]:
    """[(путь или '', тело блока)] в порядке объявления."""
    source = _read(CADDYFILE)
    return [
        (m.group(1) or "", _block_at(source, source.index("{", m.start())))
        for m in CADDY_HANDLE.finditer(source)
    ]


def _nginx_route_for(path: str) -> tuple[str, str, str] | None:
    """Первый location, который в nginx выиграет для запроса `path`.

    Упрощённая модель выбора: точное совпадение `=` бьёт всё, затем самый
    длинный префикс. Регулярных location в конфиге нет, поэтому их не учитываем.
    """
    locations = _nginx_locations()
    for modifier, pattern, body in locations:
        if modifier == "=" and pattern == path:
            return modifier, pattern, body

    prefixes = [
        (modifier, pattern, body)
        for modifier, pattern, body in locations
        if modifier in ("", "^~") and path.startswith(pattern)
    ]
    if not prefixes:
        return None
    return max(prefixes, key=lambda item: len(item[1]))


def _caddy_route_for(path: str) -> tuple[str, str] | None:
    """Первый handle, который в Caddy выиграет для запроса `path`.

    Caddy выбирает самый специфичный matcher, а `handle` без пути — фолбэк.
    """
    matched = [
        (pattern, body)
        for pattern, body in _caddy_handles()
        if pattern and (path.startswith(pattern[:-1]) if pattern.endswith("*") else pattern == path)
    ]
    if matched:
        return max(matched, key=lambda item: len(item[0]))
    for pattern, body in _caddy_handles():
        if not pattern:
            return pattern, body
    return None


def test_api_is_proxied_to_backend_in_both_configs():
    """Санити: маршрут, который уже работает, тесты видят как рабочий."""
    nginx_route = _nginx_route_for("/api/ping")
    assert nginx_route, "в miniapp/nginx.conf не нашёлся location для /api/"
    assert BACKEND_UPSTREAM in nginx_route[2], (
        f"location {nginx_route[1]} в miniapp/nginx.conf не проксирует на {BACKEND_UPSTREAM}: "
        f"{nginx_route[2]!r}"
    )

    caddy_route = _caddy_route_for("/api/ping")
    assert caddy_route and caddy_route[0], "в deploy/Caddyfile не нашёлся handle для /api/*"
    assert BACKEND_UPSTREAM in caddy_route[1], (
        f"handle {caddy_route[0]} в deploy/Caddyfile не проксирует на {BACKEND_UPSTREAM}: "
        f"{caddy_route[1]!r}"
    )


def test_nginx_routes_backend_paths_to_backend():
    """`/docs`, `/openapi.json` и `/webhook/` не должны попадать в SPA-фолбэк."""
    for path in ("/docs", "/docs/oauth2-redirect", "/openapi.json", "/webhook/max"):
        route = _nginx_route_for(path)
        assert route, (
            f"в miniapp/nginx.conf нет location для {path} — запрос уйдёт в SPA-фолбэк "
            "`try_files $uri /index.html` и вернёт 200 с HTML мини-аппа вместо ответа бэкенда"
        )
        modifier, pattern, body = route
        assert BACKEND_UPSTREAM in body, (
            f"{path} обрабатывается location {modifier} {pattern} в miniapp/nginx.conf, "
            f"а он не проксирует на {BACKEND_UPSTREAM}: {body!r}. "
            "Нужен отдельный location с `proxy_pass http://backend:8000;` "
            "по образцу существующего /api/."
        )


def test_caddy_routes_docs_and_openapi_to_backend():
    """В прод-профиле `/docs` и `/openapi.json` тоже должны уходить на бэкенд."""
    for path in ("/docs", "/docs/oauth2-redirect", "/openapi.json"):
        route = _caddy_route_for(path)
        assert route, f"в deploy/Caddyfile нет ни одного handle, подходящего под {path}"
        pattern, body = route
        assert pattern, (
            f"{path} в deploy/Caddyfile попадает в общий `handle` и уходит на miniapp:80 — "
            "панель с API за HTTPS-прокси недоступна. Нужен `handle /docs*` "
            "и `handle /openapi.json` с `reverse_proxy backend:8000`."
        )
        assert BACKEND_UPSTREAM in body, (
            f"{path} обрабатывается handle {pattern} в deploy/Caddyfile, "
            f"а он не проксирует на {BACKEND_UPSTREAM}: {body!r}"
        )
