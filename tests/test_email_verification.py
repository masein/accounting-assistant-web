"""Signup email verification.

The central rule: verification is required **only when the server can actually
send mail**. With no SMTP configured there is no way to confirm anything, so
demanding it would lock a user out of the account they just created — which is
worse than not verifying. Air-gapped installs must keep working.

A pending token is what gates login, so super-admin-provisioned users (no
token, often no address) are untouched.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.models.account import Account
from app.models.company import Company
from app.models.user import User
from app.db.tenant import tenant_bypass
from app.services import email_verification as ev

PW = "verify-pass-2026"


@pytest.fixture
def signup_enabled():
    original = settings.allow_self_signup
    settings.allow_self_signup = True
    yield
    settings.allow_self_signup = original


@pytest.fixture
def mail_on(monkeypatch):
    """Pretend SMTP is configured, and capture what would have been sent."""
    sent: list[dict] = []
    monkeypatch.setattr(ev, "mail_configured", lambda: True)
    monkeypatch.setattr(ev, "send_email",
                        lambda **kw: (sent.append(kw), True)[1])
    return sent


@pytest.fixture(autouse=True)
def _cleanup(db):
    from app.api.auth import _signup_limiter, _login_limiter

    _signup_limiter._hits.clear()
    _login_limiter._hits.clear()
    with tenant_bypass():
        before = {c.id for c in db.execute(select(Company)).scalars().all()}
    yield
    with tenant_bypass():
        fresh = [c for c in db.execute(select(Company)).scalars().all() if c.id not in before]
        for company in fresh:
            db.execute(update(Account).where(Account.company_id == company.id).values(parent_id=None))
            db.execute(delete(Account).where(Account.company_id == company.id))
            db.execute(delete(User).where(User.company_id == company.id))
            db.execute(delete(Company).where(Company.id == company.id))
        db.commit()
    _signup_limiter._hits.clear()
    _login_limiter._hits.clear()


def _user(db, username: str) -> User:
    with tenant_bypass():
        return db.execute(select(User).where(User.username == username)).scalars().one()


# ---------------------------------------------------------------------------
# Without mail: unchanged behaviour
# ---------------------------------------------------------------------------
def test_without_smtp_signup_still_signs_you_straight_in(client, db, signup_enabled):
    """An air-gapped install must not start demanding confirmation it cannot
    deliver."""
    r = client.post("/auth/signup", json={"username": "offliner", "password": PW})
    assert r.status_code == 201
    assert r.json().get("pending_verification") is not True
    assert settings.auth_cookie_name in r.cookies
    assert _user(db, "offliner").verification_token is None


def test_without_smtp_an_email_is_still_recorded_if_offered(client, db, signup_enabled):
    client.post("/auth/signup", json={
        "username": "offliner2", "password": PW, "email": "me@example.com"})
    assert _user(db, "offliner2").email == "me@example.com"


# ---------------------------------------------------------------------------
# With mail: confirmation required
# ---------------------------------------------------------------------------
def test_signup_sends_a_link_and_withholds_the_session(client, db, signup_enabled, mail_on):
    r = client.post("/auth/signup", json={
        "username": "newbie", "password": PW, "email": "newbie@example.com"})
    assert r.status_code == 201
    assert r.json()["pending_verification"] is True
    assert settings.auth_cookie_name not in r.cookies   # not signed in yet

    assert len(mail_on) == 1
    assert mail_on[0]["to"] == "newbie@example.com"
    user = _user(db, "newbie")
    assert user.verification_token
    assert user.verification_token in mail_on[0]["text"]


def test_an_email_is_required_when_mail_works(client, db, signup_enabled, mail_on):
    r = client.post("/auth/signup", json={"username": "noaddress", "password": PW})
    assert r.status_code == 400
    with tenant_bypass():
        assert db.execute(
            select(User).where(User.username == "noaddress")
        ).scalars().first() is None


def test_a_malformed_email_is_rejected(client, signup_enabled, mail_on):
    r = client.post("/auth/signup", json={
        "username": "badmail", "password": PW, "email": "not-an-address"})
    assert r.status_code == 400


def test_login_is_refused_until_confirmed(client, signup_enabled, mail_on):
    client.post("/auth/signup", json={
        "username": "waiting", "password": PW, "email": "waiting@example.com"})
    r = client.post("/auth/login", json={"username": "waiting", "password": PW})
    assert r.status_code == 403
    assert "confirm" in r.json()["detail"].lower()


def test_confirming_the_link_then_signing_in(client, db, signup_enabled, mail_on):
    client.post("/auth/signup", json={
        "username": "confirmer", "password": PW, "email": "c@example.com"})
    token = _user(db, "confirmer").verification_token

    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert "verified=1" in r.headers["location"]

    db.expire_all()
    user = _user(db, "confirmer")
    assert user.verification_token is None
    assert user.email_verified_at is not None

    assert client.post("/auth/login",
                       json={"username": "confirmer", "password": PW}).status_code == 200


# ---------------------------------------------------------------------------
# Bad tokens
# ---------------------------------------------------------------------------
def test_an_unknown_token_is_rejected(client):
    r = client.get("/auth/verify?token=nonsense", follow_redirects=False)
    assert r.status_code == 303 and "verified=0" in r.headers["location"]


def test_a_missing_token_is_rejected(client):
    r = client.get("/auth/verify", follow_redirects=False)
    assert "verified=0" in r.headers["location"]


def test_a_token_cannot_be_used_twice(client, db, signup_enabled, mail_on):
    client.post("/auth/signup", json={
        "username": "onceonly", "password": PW, "email": "o@example.com"})
    token = _user(db, "onceonly").verification_token
    client.get(f"/auth/verify?token={token}", follow_redirects=False)

    again = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert "verified=0" in again.headers["location"]


def test_an_expired_token_is_refused(client, db, signup_enabled, mail_on):
    client.post("/auth/signup", json={
        "username": "stale", "password": PW, "email": "s@example.com"})
    user = _user(db, "stale")
    user.verification_sent_at = datetime.now(timezone.utc) - timedelta(
        hours=ev.TOKEN_TTL_HOURS + 1)
    db.commit()

    ok, message = ev.consume_token(db, user.verification_token)
    assert ok is False and "expired" in message.lower()


# ---------------------------------------------------------------------------
# Resend
# ---------------------------------------------------------------------------
def test_resend_issues_a_new_link(client, db, signup_enabled, mail_on):
    client.post("/auth/signup", json={
        "username": "resender", "password": PW, "email": "r@example.com"})
    first = _user(db, "resender").verification_token
    mail_on.clear()

    r = client.post("/auth/resend-verification",
                    json={"username": "resender", "password": PW})
    assert r.status_code == 200
    db.expire_all()
    assert _user(db, "resender").verification_token != first
    assert len(mail_on) == 1


def test_resend_says_the_same_thing_for_an_unknown_account(client, mail_on):
    """Otherwise it would confirm which usernames exist."""
    known = client.post("/auth/resend-verification",
                        json={"username": "ghost-account", "password": "whatever1"})
    assert known.status_code == 200
    assert "if that account" in known.json()["message"].lower()
    assert mail_on == []


def test_resend_needs_the_right_password(client, db, signup_enabled, mail_on):
    client.post("/auth/signup", json={
        "username": "guarded", "password": PW, "email": "g@example.com"})
    token = _user(db, "guarded").verification_token
    mail_on.clear()

    client.post("/auth/resend-verification",
                json={"username": "guarded", "password": "wrong-password"})
    db.expire_all()
    assert _user(db, "guarded").verification_token == token   # unchanged
    assert mail_on == []


# ---------------------------------------------------------------------------
# Existing users are untouched
# ---------------------------------------------------------------------------
def test_a_provisioned_user_without_a_token_can_still_log_in(client, db, mail_on):
    """Super-admin-provisioned accounts have no address and no token; turning
    mail on must not lock them out."""
    from app.services.company_service import provision_company

    provision_company(db, name="Provisioned Co", locale="ir", base_currency="IRR",
                      username="provisioned", password=PW)
    db.commit()
    assert ev.awaiting_verification(_user(db, "provisioned")) is False
    assert client.post("/auth/login",
                       json={"username": "provisioned", "password": PW}).status_code == 200
