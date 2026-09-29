"""Выгружает контракт API в ../openapi.yaml: python scripts/export_openapi.py"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

# Адрес проверяемого API — только в файле контракта: живой /openapi.json остаётся без servers,
# иначе Swagger на локальном запуске слал бы запросы в прод.
SERVERS = [{"url": "https://vse-uspel.ru", "description": "Прод (адрес из DATA-API.yaml)"}]

schema = {**app.openapi()}
schema = {
    "openapi": schema.pop("openapi"),
    "info": schema.pop("info"),
    "servers": SERVERS,
    **schema,
}

out = ROOT.parent / "openapi.yaml"
out.write_text(yaml.safe_dump(schema, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"written {out}")
