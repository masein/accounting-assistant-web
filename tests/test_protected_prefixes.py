"""Security review 2026-09-24 M1/L9: the protected-prefix list was kept by hand
and missed five routers, which therefore skipped CSRF, the global rate limit
and the default-password lock. It is now derived from the registered routers;
login/logout refuse cross-site POSTs."""
from __future__ import annotations

import pytest

from app import main as main_mod
from app.main import PROTECTED_API_PREFIXES, app

EXEMPT = ("/auth/login", "/auth/logout", "/auth/signup", "/auth/verify", "/auth/resend", "/api/v1/", "/health", "/docs", "/redoc", "/openapi.json")


def test_every_api_route_is_behind_a_protected_prefix():
    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path in ("/", "/login", "/health") or path.startswith(EXEMPT) or path.startswith(("/static", "/uploads")):
            continue
        if not path.startswith(PROTECTED_API_PREFIXES):
            unprotected.append(path)
    assert not unprotected, f"routes outside every protected prefix: {sorted(set(unprotected))}"


@pytest.mark.parametrize("prefix", ["/commitments", "/insights", "/personal", "/migration", "/petty-cash", "/admin/companies", "/auth/whats-new"])
def test_previously_missing_prefixes_are_protected(prefix):
    assert prefix.startswith(PROTECTED_API_PREFIXES) or prefix in PROTECTED_API_PREFIXES


@pytest.mark.parametrize("path", ["/commitments/plans", "/petty-cash/accounts", "/migration/pending/x/dismiss", "/auth/whats-new/seen"])
def test_writes_on_those_routers_need_a_csrf_token(auth_client, client, path):
    # raw client call: cookie present, no X-CSRF-Token header → refused before the handler
    r = client.post(path, json={})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"], (path, r.status_code, r.text)
    # with the token the guard lets it through to the handler (404/422/201… — anything but the CSRF 403)
    r2 = auth_client.post(path, json={})
    assert not (r2.status_code == 403 and "CSRF" in r2.text), (path, r2.text)


def test_anonymous_calls_on_those_routers_are_401(client):
    client.cookies.clear()
    for path in ("/commitments", "/insights", "/personal/holdings", "/petty-cash/accounts", "/migration/batches"):
        assert client.get(path).status_code == 401, path


def test_login_refuses_cross_site_posts(client):
    client.cookies.clear()
    r = client.post("/auth/login", json={"username": "x", "password": "y"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/auth/login", json={"username": "x", "password": "y"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 401  # same origin: reaches the handler (wrong password)
    r = client.post("/auth/logout", headers={"Referer": "https://evil.example/page"})
    assert r.status_code == 403
    assert client.post("/auth/logout").status_code == 200  # no Origin/Referer (non-browser) is fine
