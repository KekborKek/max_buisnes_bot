"""Выгружает контракт API в ../openapi.yaml: python scripts/export_openapi.py"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

out = ROOT.parent / "openapi.yaml"
out.write_text(yaml.safe_dump(app.openapi(), allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"written {out}")
