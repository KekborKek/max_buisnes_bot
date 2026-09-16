"""Тексты для пользователя живут в content/texts.yaml. В коде тексты не хардкодим."""

from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings


@lru_cache
def _load() -> dict:
    path = Path(get_settings().content_dir) / "texts.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def t(key: str, **kwargs: object) -> str:
    """t("start.greeting", name="Борис") — берёт текст по ключу вида "раздел.ключ"."""
    node: object = _load()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return f"[{key}]"  # видно сразу, что текста не хватает
        node = node[part]
    return str(node).format(**kwargs) if kwargs else str(node)
