"""Тексты для пользователя живут в content/texts.yaml. В коде тексты не хардкодим."""

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings

log = logging.getLogger(__name__)

# Промах по ключу видно в сообщении пользователю ("[key]"), но без лога об этом никто, кроме
# пользователя, не узнает (issue #21). Один и тот же недостающий ключ дёргается на каждый
# апдейт — предупреждаем один раз за процесс, а не на каждый вызов.
_warned_missing_keys: set[str] = set()


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
            if key not in _warned_missing_keys:
                _warned_missing_keys.add(key)
                log.warning("нет текста для ключа %r в content/texts.yaml", key)
            return f"[{key}]"  # видно сразу, что текста не хватает
        node = node[part]
    return str(node).format(**kwargs) if kwargs else str(node)
