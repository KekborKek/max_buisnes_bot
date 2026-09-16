from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_webhook_rejects_bad_secret():
    with TestClient(app) as client:
        r = client.post("/webhook/max", json={}, headers={"X-Max-Bot-Api-Secret": "wrong"})
    assert r.status_code == 403
