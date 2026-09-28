"""Issue #7: маршруты бэкенда недоступны через прокси — их съедает SPA-фолбэк.

Мини-апп и бэкенд живут на одном домене, но наружу смотрит прокси: локально это
nginx из `miniapp/nginx.conf`, в проде — Caddy из `deploy/Caddyfile`. Оба
проксировали на бэкенд только часть маршрутов, а всё остальное отдавали
мини-аппу: в nginx — фолбэком `try_files $uri /index.html`. Поэтому
`http://localhost:8080/docs` отвечал 200 с HTML мини-аппа — поломка ничем
не выдавала себя, панель с API просто «пропадала».

Поднимать Docker в pytest нельзя, поэтому инвариант проверяется статически по
самим конфигам, и сразу в обе стороны: маршруты бэкенда обязаны уходить на
бэкенд, а всё остальное — оставаться у мини-аппа. Без второй половины тест
проходил бы и на конфиге, который проксирует на бэкенд вообще всё.

Список маршрутов ниже должен совпадать с `app.routes` из `backend/app/main.py`.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = ROOT / "miniapp" / "nginx.conf"
CADDYFILE = ROOT / "deploy" / "Caddyfile"

# Маршруты бэкенда. `/docs` — точный путь: main.py создаёт FastAPI с docs_url=None
# и вешает свой @app.get("/docs"), подпутей (включая oauth2-redirect) у него нет.
BACKEND_PATHS = (
    "/docs",
    "/openapi.json",
    "/redoc",
    "/health",
    "/api/me",
    "/api/ics/1-abc.ics",
    "/webhook/max",
)
# Всё остальное — мини-апп: и статика, и любой маршрут SPA.
MINIAPP_PATHS = ("/", "/index.html", "/assets/index.js", "/profile", "/docs-help")

# Директива целиком, с начала строки: закомментированную `# proxy_pass ...` не примет.
# Завершающий слэш в upstream осознанно не разрешён — он переписывает URI, и на бэкенд
# вместо /docs ушёл бы /.
NGINX_PROXY_PASS = re.compile(r"^\s*proxy_pass\s+http://backend:8000\s*;", re.MULTILINE)
NGINX_TRY_FILES = re.compile(r"^\s*try_files\s", re.MULTILINE)
CADDY_REVERSE_PROXY = re.compile(r"^\s*reverse_proxy\s+backend:8000\s*$", re.MULTILINE)
CADDY_MINIAPP_PROXY = re.compile(r"^\s*reverse_proxy\s+miniapp:80\s*$", re.MULTILINE)

# location [= | ^~ | ~ | ~*] /путь {   — модификатор необязателен
NGINX_LOCATION = re.compile(r"^\s*location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{", re.MULTILINE)
# handle /путь* {  или handle {  (фолбэк). handle_path ловим отдельно — он срезает префикс.
CADDY_HANDLE = re.compile(r"^\s*(handle|handle_path)(?:\s+(\S+))?\s*\{", re.MULTILINE)


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
    locations = [
        (m.group(1) or "", m.group(2), _block_at(source, source.index("{", m.start())))
        for m in NGINX_LOCATION.finditer(source)
    ]
    # Модель выбора ниже умеет только точные и префиксные location. Regex-локейшены в
    # nginx приоритетнее префиксных, и с ними вердикт теста был бы неверным — падаем
    # громко, а не делаем вид, что проверка всё ещё применима.
    regex_locations = [pattern for modifier, pattern, _ in locations if modifier in ("~", "~*")]
    assert not regex_locations, (
        f"в {NGINX_CONF.name} появились regex-локейшены {regex_locations}: они имеют приоритет "
        "над префиксными, и упрощённая модель выбора в этом тесте больше не применима — "
        "её нужно доработать вместе с конфигом"
    )
    return locations


def _caddy_handles() -> list[tuple[str, str, str]]:
    """[(директива, путь или '', тело блока)] в порядке объявления."""
    source = _read(CADDYFILE)
    return [
        (m.group(1), m.group(2) or "", _block_at(source, source.index("{", m.start())))
        for m in CADDY_HANDLE.finditer(source)
    ]


def _nginx_route_for(path: str) -> tuple[str, str, str]:
    """Location, который в nginx выиграет для запроса `path`.

    Модель выбора: точное совпадение `=` бьёт всё, иначе — самый длинный префикс.
    Regex-локейшены исключены проверкой в `_nginx_locations`.
    """
    locations = _nginx_locations()
    for location in locations:
        if location[0] == "=" and location[1] == path:
            return location

    prefixes = [loc for loc in locations if loc[0] in ("", "^~") and path.startswith(loc[1])]
    assert prefixes, (
        f"в {NGINX_CONF.name} нет ни одного location, подходящего под {path} — "
        "даже общего фолбэка `location /`"
    )
    return max(prefixes, key=lambda loc: len(loc[1]))


def _caddy_route_for(path: str) -> tuple[str, str, str]:
    """Handle, который в Caddy выиграет для запроса `path`.

    Caddy сортирует одноимённые директивы по специфичности matcher'а, а `handle`
    без пути работает фолбэком и уходит в конец.
    """
    handles = _caddy_handles()
    matched = [
        h
        for h in handles
        if h[1] and (path.startswith(h[1][:-1]) if h[1].endswith("*") else h[1] == path)
    ]
    if matched:
        return max(matched, key=lambda h: len(h[1]))

    fallback = [h for h in handles if not h[1]]
    assert fallback, f"в {CADDYFILE.name} нет ни handle под {path}, ни общего фолбэка `handle {{`"
    return fallback[0]


def test_api_is_proxied_to_backend_in_both_configs():
    """Санити: маршрут, который работал и до фикса, тесты видят как рабочий."""
    _, pattern, body = _nginx_route_for("/api/me")
    assert NGINX_PROXY_PASS.search(body), (
        f"location {pattern} в {NGINX_CONF.name} не проксирует на backend:8000: {body!r}"
    )

    _, pattern, body = _caddy_route_for("/api/me")
    assert pattern, f"/api/me в {CADDYFILE.name} не имеет своего handle"
    assert CADDY_REVERSE_PROXY.search(body), (
        f"handle {pattern} в {CADDYFILE.name} не проксирует на backend:8000: {body!r}"
    )


def test_nginx_routes_backend_paths_to_backend_and_keeps_spa_fallback():
    """Маршруты бэкенда — на бэкенд, всё остальное — мини-аппу."""
    for path in BACKEND_PATHS:
        modifier, pattern, body = _nginx_route_for(path)
        assert NGINX_PROXY_PASS.search(body), (
            f"{path} обрабатывается location `{modifier} {pattern}`.strip() в "
            f"{NGINX_CONF.name}, а он не проксирует на backend:8000 ровно директивой "
            f"`proxy_pass http://backend:8000;` (без завершающего слэша — он переписал бы "
            f"URI на /). Тело блока: {body!r}. Без своего location запрос уйдёт в фолбэк "
            "`try_files $uri /index.html` и вернёт 200 с HTML мини-аппа вместо ответа бэкенда."
        )

    for path in MINIAPP_PATHS:
        modifier, pattern, body = _nginx_route_for(path)
        assert NGINX_TRY_FILES.search(body) and not NGINX_PROXY_PASS.search(body), (
            f"{path} обрабатывается location `{modifier} {pattern}`.strip() в "
            f"{NGINX_CONF.name} и уходит на бэкенд вместо мини-аппа: {body!r}. "
            "Маршруты бэкенда нужно объявлять точечно, не задевая SPA-фолбэк."
        )


def test_caddy_routes_backend_paths_to_backend_and_keeps_miniapp_fallback():
    """То же для прод-профиля: за HTTPS-прокси маршруты бэкенда тоже должны быть живы."""
    for path in BACKEND_PATHS:
        directive, pattern, body = _caddy_route_for(path)
        assert pattern, (
            f"{path} в {CADDYFILE.name} попадает в общий `handle` и уходит на miniapp:80 — "
            "за прод-прокси маршрут недоступен. Нужен свой handle с "
            "`reverse_proxy backend:8000`."
        )
        assert directive == "handle", (
            f"{path} обрабатывается `{directive} {pattern}` в {CADDYFILE.name}: handle_path "
            f"срезает префикс, и на бэкенд ушёл бы путь без {pattern.rstrip('*')!r}. "
            "Нужен обычный handle."
        )
        assert CADDY_REVERSE_PROXY.search(body), (
            f"handle {pattern} в {CADDYFILE.name} не проксирует на backend:8000 ровно "
            f"директивой `reverse_proxy backend:8000`: {body!r}"
        )

    for path in MINIAPP_PATHS:
        directive, pattern, body = _caddy_route_for(path)
        assert CADDY_MINIAPP_PROXY.search(body), (
            f"{path} обрабатывается `{directive} {pattern}`.strip() в {CADDYFILE.name} "
            f"и не уходит на miniapp:80: {body!r}"
        )
