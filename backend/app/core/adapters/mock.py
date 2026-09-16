import json
from pathlib import Path

from app.core.config import get_settings


class MockDataSource:
    """ТЕСТОВЫЕ ДАННЫЕ. В интерфейсе и README это должно быть явно помечено."""

    is_mock = True

    async def lookup_company(self, inn: str) -> dict | None:
        path = Path(get_settings().seed_dir) / "companies.json"
        if not path.exists():
            return None
        companies = json.loads(path.read_text(encoding="utf-8"))
        return next((c for c in companies if c.get("inn") == inn), None)
