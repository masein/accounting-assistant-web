"""Logs, metrics and error reporting (roadmap 2026-09 §2.4).

* Every log line carries the request id (and the company) through a context
  variable, so a user's "error id" finds the whole story in the logs.
  ``LOG_FORMAT=json`` (the prod default) prints one JSON object per line.
* Prometheus metrics: HTTP requests by route template (never the raw path,
  which would explode the label set), LLM calls and background jobs. Served
  at ``/metrics`` only to a scraper holding ``METRICS_TOKEN``. With several
  workers set ``PROMETHEUS_MULTIPROC_DIR`` so the numbers are aggregated.
* Sentry (or GlitchTip) when ``SENTRY_DSN`` is set: exceptions with the
  request id as a tag; no cookies, headers or bodies are sent.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import traceback
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def clean_request_id(incoming: str | None) -> str | None:
    """A client may pass its own X-Request-ID (to correlate with its logs);
    anything that isn't a short token is ignored rather than logged."""
    value = (incoming or "").strip()
    return value if _REQUEST_ID_OK.match(value) else None


def current_request_id() -> str | None:
    return request_id_var.get()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class ContextFilter(logging.Filter):
    """Stamps request id and company on every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        try:
            from app.db.tenant import get_current_company
            record.company_id = get_current_company() or "-"
        except Exception:  # pragma: no cover - during interpreter shutdown
            record.company_id = "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "company_id": getattr(record, "company_id", "-"),
        }
        for key in ("method", "path", "route", "status", "ms", "user_id"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = "".join(traceback.format_exception(*record.exc_info))[-8000:]
        return json.dumps(out, ensure_ascii=False, default=str)


TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s rid=%(request_id)s %(message)s"


def configure_logging(fmt: str | None = None, level: str | None = None, env: str | None = None) -> None:
    """Replace the root handler with one that knows the request context.
    Idempotent; uvicorn's own loggers propagate to it."""
    from app.core.config import settings
    fmt = (fmt or settings.log_format or ("json" if (env or settings.app_env) == "prod" else "text")).lower()
    level = (level or settings.log_level or "INFO").upper()
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    handler._aa_observability = True  # type: ignore[attr-defined]
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_aa_observability", False) or isinstance(h, logging.StreamHandler):
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # uvicorn's access log duplicates app.request; keep it quiet.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_BUCKETS_HTTP = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_BUCKETS_SLOW = (0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)

try:
    from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
    from prometheus_client import CONTENT_TYPE_LATEST
    _PROM = True
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    _PROM = False

if _PROM:
    HTTP_REQUESTS = Counter("aa_http_requests_total", "HTTP requests", ["method", "route", "status"])
    HTTP_LATENCY = Histogram("aa_http_request_duration_seconds", "HTTP request latency",
                             ["method", "route"], buckets=_BUCKETS_HTTP)
    LLM_CALLS = Counter("aa_llm_calls_total", "LLM / OCR provider calls", ["provider", "purpose", "outcome"])
    LLM_LATENCY = Histogram("aa_llm_call_duration_seconds", "LLM / OCR call latency",
                            ["provider", "purpose"], buckets=_BUCKETS_SLOW)
    JOB_RUNS = Counter("aa_job_runs_total", "Background job runs per company", ["job", "outcome"])
    JOB_LATENCY = Histogram("aa_job_duration_seconds", "Background job duration per company",
                            ["job"], buckets=_BUCKETS_SLOW)


def route_label(method: str, path: str) -> str:
    """The route template ("/invoices/{invoice_id}") for a concrete path, so a
    label never carries ids. Unknown paths collapse into one bucket."""
    if path.startswith("/static/"):
        return "/static/*"
    try:
        from app.core.permissions import resolve_template
        tpl = resolve_template(method, path)
    except Exception:
        tpl = None
    if tpl:
        return tpl
    if path in ("/", "/health", "/login", "/metrics") or path.startswith(("/auth/", "/api/v1/")):
        return re.sub(r"/[0-9a-fA-F-]{16,}", "/{id}", path)
    return "other"


def observe_request(method: str, path: str, status: int, seconds: float) -> None:
    if not _PROM:
        return
    route = route_label(method, path)
    HTTP_REQUESTS.labels(method, route, str(status)).inc()
    HTTP_LATENCY.labels(method, route).observe(seconds)


@asynccontextmanager
async def observe_llm(provider: str, purpose: str):
    """Wrap one provider call: counts ok/error and times it."""
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        if _PROM:
            LLM_CALLS.labels(provider or "unknown", purpose, outcome).inc()
            LLM_LATENCY.labels(provider or "unknown", purpose).observe(time.perf_counter() - started)


@contextmanager
def observe_job(job: str):
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        if _PROM:
            JOB_RUNS.labels(job, outcome).inc()
            JOB_LATENCY.labels(job).observe(time.perf_counter() - started)


def metrics_payload() -> tuple[bytes, str]:
    """Exposition text. Aggregated across workers when multiprocess mode is on."""
    if not _PROM:
        return b"", "text/plain"
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry), CONTENT_TYPE_LATEST
    from prometheus_client import REGISTRY
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------

_sentry_on = False


def _scrub(event: dict, _hint: dict) -> dict | None:
    """Never ship credentials or bodies: drop cookies, auth headers, request
    bodies; keep method, route and the request id."""
    req = event.get("request") or {}
    for key in ("cookies", "data", "query_string", "env"):
        req.pop(key, None)
    headers = req.get("headers") or {}
    req["headers"] = {k: v for k, v in headers.items()
                      if k.lower() not in ("cookie", "authorization", "x-api-key", "x-csrf-token")}
    event["request"] = req
    rid = request_id_var.get()
    if rid:
        event.setdefault("tags", {})["request_id"] = rid
    return event


def init_error_reporting() -> bool:
    """Start Sentry when SENTRY_DSN is set. Returns whether it is on."""
    global _sentry_on
    from app.core.config import settings
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn or _sentry_on:
        return _sentry_on
    try:
        import sentry_sdk
        from app.core.release_notes import CURRENT_RELEASE
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app_env,
            release=f"accounting-assistant@{CURRENT_RELEASE}",
            send_default_pii=False,
            traces_sample_rate=float(settings.sentry_traces_sample_rate or 0.0),
            before_send=_scrub,
        )
        _sentry_on = True
    except Exception:  # never let monitoring stop the app
        logging.getLogger("app.observability").warning("Sentry init failed", exc_info=True)
    return _sentry_on
