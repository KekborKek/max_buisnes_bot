"""Issue #27: без корня Russian Trusted Root CA httpx не проверит TLS platform-api2.max.ru."""

import hashlib
import os
import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from app.core.max_client import MaxClient, _build_verify

CA_BUNDLE = Path(__file__).resolve().parents[2] / "deploy" / "certs" / "russian_trusted_root_ca.crt"
# отпечаток из issue #27 — если он не совпадает с файлом в репозитории, значит подсунули не тот
# сертификат (так уже раз случилось: прислали корень ГОСТ-2025 вместо этого)
PINNED_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def test_ca_bundle_file_matches_pinned_fingerprint():
    pem = CA_BUNDLE.read_text()
    der = ssl.PEM_cert_to_DER_cert(pem)
    assert hashlib.sha256(der).hexdigest() == PINNED_SHA256


def test_build_verify_empty_keeps_default_httpx_behaviour():
    assert _build_verify("") is True


def test_build_verify_adds_root_without_losing_public_cas():
    baseline = ssl.create_default_context(cafile=certifi.where())
    baseline_count = baseline.cert_store_stats()["x509"]

    ctx = _build_verify(str(CA_BUNDLE))

    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.cert_store_stats()["x509"] == baseline_count + 1


async def test_max_client_passes_ssl_context_to_httpx(monkeypatch):
    monkeypatch.setenv("MAX_CA_BUNDLE", str(CA_BUNDLE))
    from app.core import config

    config.get_settings.cache_clear()
    captured = {}
    real_init = httpx.AsyncClient.__init__

    def spy_init(self, *args, **kwargs):
        captured["verify"] = kwargs.get("verify")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)
    try:
        client = MaxClient(token="t", base_url="https://example.invalid")
        await client.close()
    finally:
        config.get_settings.cache_clear()

    assert isinstance(captured["verify"], ssl.SSLContext)


@pytest.mark.skipif(
    not os.environ.get("RUN_MAX_NETWORK_TESTS"),
    reason="сетевой тест: нужен доступ к platform-api2.max.ru, в CI его нет",
)
async def test_max_api_tls_handshake_succeeds():
    """Не про содержимое ответа — важно только, что TLS поднялся (не CERTIFICATE_VERIFY_FAILED)."""
    client = MaxClient(token="", base_url="https://platform-api2.max.ru")
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.get_me()
        assert exc_info.value.response.status_code == 401
    finally:
        await client.close()
