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


def test_the_digest_is_written_in_its_readers_language(db):
    from app.models.user import User

    co = _company(db)
    with use_company(str(co.id)):
        set_digest_settings(db, enabled=True, cash_threshold=1_000_000)
        d = build_daily_digest(db)
    fa = format_digest("آکمه", d, "fa")
    assert fa.startswith("خلاصه روزانه — آکمه") and "نقدینگی کم (نقد کمتر از آستانه)" in fa and "موجودی نقد: ۰ " in fa   # Persian digits
    assert format_digest("Acme", d, "es").startswith("Resumen diario — Acme")
    en = format_digest("Acme", d)
    assert "LOW CASH (cash below the threshold)" in en and "Cash on hand: 0" in en
    # the owner's language, else the company's
    from app.services.digest_service import digest_language
    with use_company(str(co.id)):
        assert digest_language(db) == "en"                       # a UK company, no owner yet
        owner = User(username=f"own-{uuid.uuid4().hex[:6]}", password_hash="x", password_salt="x", role="owner",
                     is_active=True, company_id=co.id, preferred_language="fa")
        db.add(owner)
        db.flush()
        assert digest_language(db) == "fa"
    db.delete(owner)
    db.flush()


def test_the_digest_email_is_titled_in_its_language(db, client, monkeypatch):
    from app.models.user import User
    from app.services import mail_service

    co = _company(db)
    owner = User(username=f"own-{uuid.uuid4().hex[:6]}", password_hash="x", password_salt="x", role="owner",
                 is_active=True, company_id=co.id, preferred_language="fa")
    db.add(owner)
    db.commit()
    with use_company(str(co.id)):
        set_digest_settings(db, enabled=True, channel="email")
        db.commit()
    sent = []
    from app.api import notifications
    # the operator's channel carries only the platform's own company (review
    # 2026-11-16); this test is about the subject's language
    monkeypatch.setattr(notifications, "_operator_channels_open", lambda: True)
    monkeypatch.setattr(settings, "smtp_to", "ops@example.com", raising=False)
    monkeypatch.setattr(mail_service, "send_email", lambda **kw: sent.append(kw) or True)
    r = _api(client, Role.OWNER, co).post("/notifications/daily-digest", json={})
    assert r.status_code == 200 and "email" in r.json()["delivered"], r.text
    assert sent[0]["subject"] == "خلاصه روزانه — Acme" and sent[0]["text"].startswith("خلاصه روزانه")
    db.delete(owner)
    db.commit()


def test_another_companys_digest_never_reaches_the_operators_channels(db, client, monkeypatch):
    """Review 2026-11-16: SLACK_WEBHOOK_URL / TELEGRAM_* / SMTP_TO are the
    operator's, one set per server — every company's digest (and its alerts
    from /notifications/check) went to them."""
    import asyncio

    from app.api import notifications
    from app.db.seed import DEFAULT_COMPANY_ID
    from app.services import mail_service

    posted, mailed = [], []

    class _Http:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            posted.append(url)
            return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(notifications.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.example/ops", raising=False)
    monkeypatch.setattr(settings, "telegram_bot_token", "tok", raising=False)
    monkeypatch.setattr(settings, "telegram_chat_id", "42", raising=False)
    monkeypatch.setattr(settings, "smtp_to", "ops@example.com", raising=False)
    monkeypatch.setattr(mail_service, "send_email", lambda **kw: mailed.append(kw) or True)

    co = _company(db)
    db.commit()
    with use_company(str(co.id)):
        set_digest_settings(db, enabled=True, channel="all")
        db.commit()
    r = _api(client, Role.OWNER, co).post("/notifications/daily-digest", json={})
    assert r.status_code == 200 and r.json()["delivered"] == [], r.text
    with use_company(str(co.id)):
        assert asyncio.run(notifications._send_slack("x")) is False
        assert asyncio.run(notifications._send_telegram("x")) is False
        assert notifications._send_email("x") is False
    assert posted == [] and mailed == []

    with use_company(DEFAULT_COMPANY_ID):                    # the platform's own books: as before
        assert asyncio.run(notifications._send_slack("x")) is True
        assert notifications._send_email("x") is True
    assert posted == ["https://hooks.example/ops"] and len(mailed) == 1
