import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from app.core.initdata import InitDataError, validate_init_data

TOKEN = "test-token"


def sign(params: dict) -> str:
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": h})


def test_valid_init_data():
    raw = sign({"auth_date": str(int(time.time())), "user": '{"user_id": 42, "first_name": "A"}'})
    data = validate_init_data(raw, TOKEN)
    assert data["user"]["user_id"] == 42


def test_tampered_init_data():
    raw = sign({"auth_date": str(int(time.time())), "user": '{"user_id": 42}'})
    with pytest.raises(InitDataError):
        validate_init_data(raw.replace("42", "43"), TOKEN)
