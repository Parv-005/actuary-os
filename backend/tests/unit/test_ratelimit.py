from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.ratelimit import RateLimitMiddleware


def _app(limits):
    a = FastAPI()

    @a.get("/workflows/x/upload")
    def upload():
        return {"ok": True}

    @a.post("/workflows")
    def create():
        return {"ok": True}

    @a.get("/workflows")
    def list_wf():
        return {"ok": True}

    return RateLimitMiddleware(a, limits=limits)


def test_upload_bucket_limit():
    client = TestClient(_app({"uploads": (2, 60.0), "starts": (5, 60.0), "default": (60, 60.0)}))
    assert client.get("/workflows/x/upload").status_code == 200
    assert client.get("/workflows/x/upload").status_code == 200
    assert client.get("/workflows/x/upload").status_code == 429


def test_starts_bucket_applies_to_create():
    client = TestClient(_app({"uploads": (10, 60.0), "starts": (1, 60.0), "default": (60, 60.0)}))
    assert client.post("/workflows").status_code == 200
    assert client.post("/workflows").status_code == 429
    assert client.get("/workflows").status_code == 200  # GET list uses default bucket


def test_429_shape():
    client = TestClient(_app({"uploads": (0, 60.0), "starts": (5, 60.0), "default": (60, 60.0)}))
    r = client.get("/workflows/x/upload")
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
