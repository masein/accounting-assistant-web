"""Live RBAC on EVERY mutating route × EVERY company role (roadmap 2026-09 §6,
suite 1). The matrix test proves the rule table; this proves the guard is
wired in front of each route: a real request with the role's session gets
403 exactly when `user_can_access` denies, and never 401/403 when it allows.
Bodies are empty, so allowed calls mostly end in 404/422 — that is fine, the
point is that the decision happened in the guard, not in the handler."""
from __future__ import annotations

import re
import uuid

import pytest

from app.core.permissions import ALL_ROLES, user_can_access
from app.core.rate_limit import RateLimiter
from app.main import app
from tests.test_rbac_permissions import _role_client, _user

# Not part of the RBAC table (own guards) or not routes at all.
EXEMPT_PREFIXES = ("/auth/", "/api/v1/", "/docs", "/redoc", "/openapi.json", "/static/", "/favicon")
EXEMPT_EXACT = {"/", "/login", "/health"}
# Allowed for the owner and would wreck the shared test database or send mail.
DESTRUCTIVE = {("POST", "/admin/reset-db"), ("POST", "/brain/cfo/seed-sample-data"), ("POST", "/admin/test-email")}


def _mutating_routes() -> list[tuple[str, str]]:
    out = []
    for path, ops in app.openapi()["paths"].items():
        if path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIXES):
            continue
        for method in ops:
            m = method.upper()
            if m in ("GET", "HEAD", "OPTIONS") or (m, path) in DESTRUCTIVE:
                continue
            out.append((m, path))
    return sorted(out)


def _concrete(template: str) -> str:
    return re.sub(r"\{([^}]+)\}", lambda m: str(uuid.uuid4()) if "id" in m.group(1).lower() else "x", template)


@pytest.fixture(autouse=True)
def _no_api_rate_limit(monkeypatch):
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_api_limiter", RateLimiter(max_requests=100_000, window_seconds=60))


def test_there_are_many_mutating_routes():
    assert len(_mutating_routes()) > 100


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_mutating_route_is_guarded_for(client, role):
    rc = _role_client(client, role)
    wrong_deny, wrong_allow, crashed = [], [], []
    for method, template in _mutating_routes():
        expected_allowed = user_can_access(_user(role), method, template)
        url = _concrete(template)
        kwargs = {} if method == "DELETE" else {"json": {}}
        resp = getattr(rc, method.lower())(url, **kwargs)
        if expected_allowed and resp.status_code in (401, 403):
            wrong_deny.append((method, template, resp.status_code))
        if not expected_allowed and resp.status_code != 403:
            wrong_allow.append((method, template, resp.status_code))
        if resp.status_code >= 500:
            crashed.append((method, template, resp.status_code))
    assert not wrong_allow, f"{role}: reached routes the matrix denies: {wrong_allow}"
    assert not wrong_deny, f"{role}: guard blocked routes the matrix allows: {wrong_deny}"
    assert not crashed, f"{role}: 5xx on an empty body (handler crash, not validation): {crashed}"
