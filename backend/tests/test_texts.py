"""LEAD-9 (#21): пропущенный ключ t(...) не должен быть виден только пользователю в чате.

Два слепых пятна закрываем:
1. Опечатка в ключе `t("...")` доезжает до пользователя как "[раздел.ключ]" — эту сборку
   ключей регуляркой по backend/app/ сверяем с настоящим content/texts.yaml (не фикстурой:
   у фикстур нет обязательства совпадать с реальными ключами кода).
2. В логе о промахе раньше не было ни строки — теперь есть `log.warning`, но один раз на
   ключ, чтобы не заспамить лог одним и тем же промахом на каждый апдейт.

Ограничение метода (осознанное, из самого issue — "собрать все литералы t(\"...\")
регуляркой"): ключи, собранные динамически (`t(f"reminder.btn_{action}")`,
`t(f"onboarding.{key}")`, `t(variable)` и т.п.), эта проверка не видит — для них нет
статического литерала, который можно было бы сравнить с yaml.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from app.core import texts as texts_module
from app.core.texts import t

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "backend" / "app"
TEXTS_PATH = ROOT / "content" / "texts.yaml"

# t("a.b.c" ...) / t('a.b.c', ...) — только строковый литерал сразу после открывающей скобки.
# \s (включая переносы строк) допускает многострочные вызовы вида t(\n    "task.confirm", ...).
_KEY_RE = re.compile(r"""\bt\(\s*["']([a-zA-Z0-9_.]+)["']""")


def _literal_keys() -> set[str]:
    keys: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        keys.update(_KEY_RE.findall(path.read_text(encoding="utf-8")))
    return keys


def _flatten(node: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _flatten(value, full)
        else:
            out.add(full)
    return out


def test_all_literal_t_keys_exist_in_texts_yaml():
    literal_keys = _literal_keys()
    assert literal_keys, 'регэксп не нашёл ни одного t("...") в backend/app — проверь сам тест'

    data = yaml.safe_load(TEXTS_PATH.read_text(encoding="utf-8")) or {}
    yaml_keys = _flatten(data)

    missing = sorted(key for key in literal_keys if key not in yaml_keys)
    assert not missing, "в коде есть ключи t(...), которых нет в content/texts.yaml: " + ", ".join(
        missing
    )


def test_missing_key_returns_bracketed_placeholder_and_logs_once(caplog):
    key = "test_texts.несуществующий_ключ"
    texts_module._warned_missing_keys.discard(key)

    with caplog.at_level(logging.WARNING, logger=texts_module.log.name):
        assert t(key) == f"[{key}]"
        assert t(key) == f"[{key}]"  # второй промах по тому же ключу — без нового варнинга

    warnings = [r for r in caplog.records if key in r.getMessage()]
    assert len(warnings) == 1, (
        f"ожидали одно предупреждение на ключ {key!r}, получили {len(warnings)}: {warnings}"
    )
