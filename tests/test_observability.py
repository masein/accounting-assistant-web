"""Observability (roadmap 2026-09 §2.4): request ids end to end, JSON logs
with request context, token-gated Prometheus metrics labelled by route
template, LLM and job metrics, and Sentry scrubbing."""
from __future__ import annotations

import json
import logging
import uuid

import pytest
from prometheus_client import REGISTRY

from app.core import observability as obs


def _count(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ─── request ids ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,ok", [
    ("abc-123_x.y", True), ("a" * 64, True), ("a" * 65, False), ("", False),
    ("with space", False), ("<script>", False), ("line\nbreak", False), (None, False),
])
def test_clean_request_id(value, ok):
    assert (obs.clean_request_id(value) is not None) is ok


def test_every_response_gets_a_request_id_even_when_rejected(client):
    r = client.get("/invoices")                      # no session: the auth middleware answers
    assert r.status_code == 401
    rid = r.headers.get("x-request-id")
    assert rid and len(rid) == 16
    mine = client.get("/invoices", headers={"x-request-id": "client-corr-42"})
    assert mine.headers["x-request-id"] == "client-corr-42"
    junk = client.get("/invoices", headers={"x-request-id": "bad id with spaces"})
    assert junk.headers["x-request-id"] != "bad id with spaces"


def test_rejected_requests_are_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        r = client.get("/invoices", headers={"x-request-id": "logged-401"})
    assert r.status_code == 401
    lines = [rec for rec in caplog.records if rec.name == "app.request" and "logged-401" in rec.getMessage()]
    assert lines and lines[0].status == 401 and lines[0].path == "/invoices"



def test_the_request_line_itself_carries_the_id(client):
    """The filter runs when the line is emitted; the id must still be set."""
    seen = []

    class _Grab(logging.Handler):
        def emit(self, record):
            seen.append(record)

    h = _Grab()
    h.addFilter(obs.ContextFilter())
    lg = logging.getLogger("app.request")
    lg.addHandler(h)
    try:
        client.get("/invoices", headers={"x-request-id": "own-line-1"})
    finally:
        lg.removeHandler(h)
    done = [r for r in seen if r.getMessage().startswith("request_done") and "own-line-1" in r.getMessage()]
    assert done and done[0].request_id == "own-line-1"


def test_validation_errors_carry_the_same_id(auth_client):
    r = auth_client.post("/invoices", json={"number": 1}, headers={"x-request-id": "val-err-1"})
    assert r.status_code == 422
    assert r.json()["request_id"] == "val-err-1" == r.headers["x-request-id"]


def test_the_id_is_in_context_while_the_handler_runs(auth_client, monkeypatch):
    from app.api import fx as fx_api
    seen = {}

    def _spy(db):
        seen["rid"] = obs.current_request_id()
        return "IRR"

    monkeypatch.setattr(fx_api, "get_reporting_currency", _spy)
    r = auth_client.get("/fx/reporting-currency")
    assert r.status_code == 200
    assert seen["rid"] == r.headers["x-request-id"]
    assert obs.current_request_id() is None          # reset after the request


def test_a_crash_answers_json_with_the_request_id(db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import fx as fx_api
    from app.db.session import get_db
    from app.main import app
    from tests.test_rbac_permissions import _role_client

    def _boom(db):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(fx_api, "get_reporting_currency", _boom)
    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app, raise_server_exceptions=False) as raw:
            c = _role_client(raw, "owner")
            r = c.get("/fx/reporting-currency", headers={"x-request-id": "crash-7"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 500
    body = r.json()
    assert body == {"code": "INTERNAL_ERROR", "detail": "Internal server error", "request_id": "crash-7"}
    assert "kaboom" not in r.text


# ─── logging ───────────────────────────────────────────────────────────

def test_context_filter_and_json_formatter():
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    token = obs.request_id_var.set("ctx-9")
    try:
        from app.db.tenant import use_company
        with use_company("c-123"):
            assert obs.ContextFilter().filter(record)
    finally:
        obs.request_id_var.reset(token)
    record.status = 201
    out = json.loads(obs.JsonFormatter().format(record))
    assert out["msg"] == "hello world" and out["level"] == "WARNING" and out["logger"] == "app.test"
    assert out["request_id"] == "ctx-9" and out["company_id"] == "c-123" and out["status"] == 201
    try:
        raise ValueError("bad")
    except ValueError:
        import sys
        err = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    obs.ContextFilter().filter(err)
    assert "ValueError: bad" in json.loads(obs.JsonFormatter().format(err))["exc"]


def test_configure_logging_switches_format_and_is_idempotent():
    root = logging.getLogger()
    try:
        obs.configure_logging(fmt="json")
        obs.configure_logging(fmt="json")
        mine = [h for h in root.handlers if getattr(h, "_aa_observability", False)]
        assert len(mine) == 1 and isinstance(mine[0].formatter, obs.JsonFormatter)
    finally:
        obs.configure_logging(fmt="text")
    mine = [h for h in root.handlers if getattr(h, "_aa_observability", False)]
    assert len(mine) == 1 and not isinstance(mine[0].formatter, obs.JsonFormatter)


# ─── metrics ───────────────────────────────────────────────────────────

def test_metrics_endpoint_is_off_without_a_token_and_gated_with_one(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "metrics_token", None)
    assert client.get("/metrics").status_code == 404
    monkeypatch.setattr(settings, "metrics_token", "s3cret-token")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 200 and "aa_http_requests_total" in r.text


def test_http_metrics_use_the_route_template_not_the_id(auth_client):
    route = "/invoices/{invoice_id}/pdf"
    before = _count("aa_http_requests_total", method="GET", route=route, status="404")
    for _ in range(2):
        assert auth_client.get(f"/invoices/{uuid.uuid4()}/pdf").status_code == 404
    assert _count("aa_http_requests_total", method="GET", route=route, status="404") == before + 2
    assert obs.route_label("GET", f"/no/such/{uuid.uuid4()}") == "other"
    assert obs.route_label("GET", "/static/js/01-core.js") == "/static/*"


@pytest.mark.asyncio
async def test_llm_metrics_count_ok_and_error():
    ok0 = _count("aa_llm_calls_total", provider="t-prov", purpose="chat", outcome="ok")
    err0 = _count("aa_llm_calls_total", provider="t-prov", purpose="chat", outcome="error")
    async with obs.observe_llm("t-prov", "chat"):
        pass
    with pytest.raises(RuntimeError):
        async with obs.observe_llm("t-prov", "chat"):
            raise RuntimeError("provider down")
    assert _count("aa_llm_calls_total", provider="t-prov", purpose="chat", outcome="ok") == ok0 + 1
    assert _count("aa_llm_calls_total", provider="t-prov", purpose="chat", outcome="error") == err0 + 1
    assert _count("aa_llm_call_duration_seconds_count", provider="t-prov", purpose="chat") >= 2


def test_job_metrics_record_failures_per_company(db, monkeypatch):
    from app.jobs import scheduler as sched
    from tests.conftest import _TestSession
    monkeypatch.setattr(sched, "_session_factory", lambda: _TestSession)
    monkeypatch.setattr(sched, "_active_company_ids", lambda _db: ["00000000-0000-0000-0000-00000000abcd"])
    err0 = _count("aa_job_runs_total", job="t_job", outcome="error")

    def _fails(_db, _today):
        raise RuntimeError("job broke")

    from datetime import date
    status = sched.run_job_for_all_companies("t_job", _fails, today=date(2026, 9, 25), once_per_day=False)
    assert status.companies_failed == 1
    assert _count("aa_job_runs_total", job="t_job", outcome="error") == err0 + 1


# ─── error reporting ───────────────────────────────────────────────────

def test_sentry_event_scrubbing():
    token = obs.request_id_var.set("rid-sentry")
    try:
        event = {"request": {"cookies": {"session": "x"}, "data": "{password}", "query_string": "token=1",
                             "headers": {"Cookie": "a", "Authorization": "Bearer t", "X-API-Key": "k",
                                         "User-Agent": "ua"}}}
        out = obs._scrub(event, {})
    finally:
        obs.request_id_var.reset(token)
    req = out["request"]
    assert "cookies" not in req and "data" not in req and "query_string" not in req
    assert req["headers"] == {"User-Agent": "ua"}
    assert out["tags"]["request_id"] == "rid-sentry"


def test_sentry_is_off_without_a_dsn_and_safe_with_one(monkeypatch):
    import sentry_sdk
    from app.core.config import settings
    monkeypatch.setattr(obs, "_sentry_on", False)
    monkeypatch.setattr(settings, "sentry_dsn", None)
    assert obs.init_error_reporting() is False
    seen = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(settings, "sentry_dsn", "https://public@glitchtip.example/1")
    assert obs.init_error_reporting() is True
    assert seen["send_default_pii"] is False and seen["before_send"] is obs._scrub
    assert seen["release"].startswith("accounting-assistant@") and seen["traces_sample_rate"] == 0.0
