"""Проверка initData мини-приложения (docs: dev.max.ru/docs/webapps/validation).

secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)
hash = hex(HMAC_SHA256(key=secret_key, msg=data_check_string)),
где data_check_string — пары key=value без hash, отсортированные по ключу, через "\n".
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    if not init_data:
        raise InitDataError("empty initData")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash is missing")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("bad hash")

    auth_date = int(pairs.get("auth_date", "0") or 0)
    if max_age_seconds and auth_date and time.time() - auth_date > max_age_seconds:
        raise InitDataError("initData is too old")

    result: dict = dict(pairs)
    for key in ("user", "chat"):
        if key in result:
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError:
                pass
    return result
