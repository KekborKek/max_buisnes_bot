"""Контракт openapi.yaml для сдачи: ответы авторизации описаны, файл совпадает с кодом.

DATA-API.yaml проверяет `/api/me → 401`, поэтому 401 должен быть в контракте у каждой
операции с заголовком X-Max-Init-Data. Лента iCalendar авторизуется токеном в пути — там 401 нет.
"""

from pathlib import Path

import yaml

from app.main import app

ROOT = Path(__file__).resolve().parents[2]
OPENAPI_FILE = ROOT / "openapi.yaml"
DATA_API_FILE = ROOT / "DATA-API.yaml"


def _operations(schema: dict):
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            yield path, method, operation


def test_401_described_for_every_init_data_operation():
    schema = app.openapi()
    for path, method, operation in _operations(schema):
        headers = {p["name"] for p in operation.get("parameters", []) if p["in"] == "header"}
        has_401 = "401" in operation["responses"]
        assert has_401 == ("x-max-init-data" in headers), f"{method.upper()} {path}"


def test_ics_feed_has_no_401():
    feed = app.openapi()["paths"]["/api/ics/{token}.ics"]["get"]
    assert "401" not in feed["responses"]


def test_health_documents_503():
    assert "503" in app.openapi()["paths"]["/health"]["get"]["responses"]


def test_openapi_file_matches_code():
    """openapi.yaml устарел — запустите `make openapi`."""
    committed = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    committed.pop("servers", None)
    generated = yaml.safe_load(yaml.safe_dump(app.openapi(), allow_unicode=True, sort_keys=False))
    assert committed == generated


def test_openapi_servers_match_data_api_base_url():
    committed = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    data_api = yaml.safe_load(DATA_API_FILE.read_text(encoding="utf-8"))
    assert committed["servers"][0]["url"] == data_api["api"]["baseUrl"]


def test_data_api_checks_exist_in_openapi_with_expected_codes():
    schema = yaml.safe_load(OPENAPI_FILE.read_text(encoding="utf-8"))
    data_api = yaml.safe_load(DATA_API_FILE.read_text(encoding="utf-8"))
    for check in data_api["checks"] + data_api["cleanup"]:
        operation = schema["paths"][check["path"]][check["method"].lower()]
        for code in check["expected"]["statusCodes"]:
            assert str(code) in operation["responses"], f"{check['id']}: {code}"
