"""Low-cash / daily digest — Owner+CFO only, safe content, low-cash flag."""
from __future__ import annotations

import uuid

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.core.permissions import Role
from app.db.tenant import use_company
from app.models.company import Company
from app.services.digest_service import (
    build_daily_digest,
    format_digest,
    get_digest_settings,
    set_digest_settings,
)
from tests.conftest import _CSRFTestClient


def _company(db):
    c = Company(id=uuid.uuid4(), name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}",
                locale="uk", base_currency="GBP", status="active", token_version=0)
    db.add(c); db.flush()
    return c


def _api(client, role, company):
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role,
                               is_admin=(role == Role.OWNER), company_id=str(company.id), role=role)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_settings_round_trip(db):
    co = _company(db)
    with use_company(str(co.id)):
        assert get_digest_settings(db)["enabled"] is False
        s = set_digest_settings(db, enabled=True, cash_threshold=500000, runway_months=2.0, channel="slack")
        assert s == {"enabled": True, "cash_threshold": 500000, "runway_months": 2.0, "channel": "slack"}
        assert get_digest_settings(db)["channel"] == "slack"


def test_invalid_channel_and_negative_rejected(db):
    co = _company(db)
    with use_company(str(co.id)):
        with pytest.raises(ValueError):
            set_digest_settings(db, channel="carrier-pigeon")
        with pytest.raises(ValueError):
            set_digest_settings(db, cash_threshold=-1)


def test_build_digest_flags_low_cash_and_is_safe(db):
    co = _company(db)
    with use_company(str(co.id)):
        set_digest_settings(db, enabled=True, cash_threshold=1_000_000)  # cash is 0 in an empty co
        d = build_daily_digest(db)
    assert d["low_cash"] is True
    assert "cash_below_threshold" in d["low_cash_reasons"]
    body = format_digest("Acme", d).lower()
    # Role-safe: no salary or bank-account detail leaks into the digest body.
    for forbidden in ("salary", "payslip", "account number", "sort code", "iban"):
        assert forbidden not in body


def test_endpoint_role_gating(db, client):
    co = _company(db)
    # Owner + CFO may trigger the digest; Accountant + Viewer may not.
    assert _api(client, Role.OWNER, co).post("/notifications/daily-digest", json={}).status_code == 200
    assert _api(client, Role.CFO, co).post("/notifications/daily-digest", json={}).status_code == 200
    assert _api(client, Role.ACCOUNTANT, co).post("/notifications/daily-digest", json={}).status_code == 403
    assert _api(client, Role.VIEWER, co).post("/notifications/daily-digest", json={}).status_code == 403


def test_disabled_digest_does_not_deliver(db, client):
    co = _company(db)
    api = _api(client, Role.OWNER, co)
    r = api.post("/notifications/daily-digest", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["delivered"] == []  # nothing configured/enabled


def test_settings_write_is_owner_only(db, client):
    co = _company(db)
    assert _api(client, Role.CFO, co).put("/notifications/digest-settings", json={"enabled": True}).status_code == 403
    assert _api(client, Role.OWNER, co).put("/notifications/digest-settings", json={"enabled": True}).status_code == 200
    # CFO can still READ settings
    assert _api(client, Role.CFO, co).get("/notifications/digest-settings").status_code == 200
