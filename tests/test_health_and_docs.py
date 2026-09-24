"""Security review 2026-09-24 M6 + L1: /health said 200 "degraded" with the
database down, so the container stayed healthy; the interactive API docs were
served in production."""
from __future__ import annotations

from app.core.config import settings


def test_health_is_200_when_the_database_answers(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["database"] == "connected"


def test_health_is_503_when_the_database_is_down(client, monkeypatch):
    import app.main as main_mod

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(main_mod, "SessionLocal", boom)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded" and r.json()["database"] == "unavailable"


def test_api_docs_are_hidden_in_production(client, monkeypatch):
    assert client.get("/openapi.json").status_code == 200  # dev/test: available
    monkeypatch.setattr(settings, "app_env", "prod")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    assert client.get("/health").status_code == 200  # everything else unaffected
