"""Phase 4 verification: /health + /health/deep with fake LLM + memory storage."""
from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert "version" in body


def test_health_deep():
    with TestClient(app) as client:
        r = client.get("/health/deep")
        assert r.status_code == 200
        body = r.json()
        assert body == {"db": True, "storage": True, "llm": True}
