"""Public self-signup.

Off by default on purpose: this app is deployed for firms, and a deployment
that quietly began accepting strangers' accounts would be a security
regression. When an operator does enable it, signup may only ever create a
*personal* tenant — never a business one with payroll, approvals and user
management attached.
"""
from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.models.account import Account
from app.models.company import Company
from app.models.user import User
from app.db.tenant import tenant_bypass, use_company

PW = "signup-pass-2026"


@pytest.fixture
def signup_enabled():
    original = settings.allow_self_signup
    settings.allow_self_signup = True
    yield
    settings.allow_self_signup = original


@pytest.fixture(autouse=True)
def _reset_limiter():
    from app.api.auth import _signup_limiter

    _signup_limiter._hits.clear()
    yield
    _signup_limiter._hits.clear()


@pytest.fixture(autouse=True)
def _remove_created_tenants(db):
    """Delete the tenants these tests provision.

    Signing up seeds a full chart of accounts. The suite shares one database,
    so leaving those behind makes a later `Account.code == "1110"` lookup match
    rows from several companies and fail with MultipleResultsFound — in tests
    that have nothing to do with signup.
    """
    with tenant_bypass():
        before = {c.id for c in db.execute(select(Company)).scalars().all()}
    yield
    with tenant_bypass():
        fresh = [c for c in db.execute(select(Company)).scalars().all() if c.id not in before]
        for company in fresh:
            # Bulk statements rather than ORM deletes: the session may already
            # have discarded some of these rows, and per-object deletes then
            # warn about matching zero rows.
            db.execute(update(Account).where(Account.company_id == company.id)
                       .values(parent_id=None))   # self-FK: unlink before delete
            db.execute(delete(Account).where(Account.company_id == company.id))
            db.execute(delete(User).where(User.company_id == company.id))
            db.execute(delete(Company).where(Company.id == company.id))
        db.commit()


def _signup(client, username: str, password: str = PW, **extra):
    return client.post("/auth/signup", json={"username": username, "password": password, **extra})


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------
def test_signup_is_refused_when_disabled(client):
    """The default. An existing SME deployment must not gain public signup
    just by upgrading."""
    assert settings.allow_self_signup is False
    r = _signup(client, "stranger")
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


def test_no_account_is_created_while_disabled(client, db):
    _signup(client, "ghost")
    with tenant_bypass():
        assert db.execute(select(User).where(User.username == "ghost")).scalars().first() is None


# ---------------------------------------------------------------------------
# What it creates
# ---------------------------------------------------------------------------
def test_signup_creates_a_personal_tenant_and_signs_in(client, db, signup_enabled):
    r = _signup(client, "newcomer")
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["user"]["username"] == "newcomer"
    assert body["user"]["role"] == "personal"
    assert body["user"]["is_admin"] is False
    assert body["user"]["is_superadmin"] is False
    # signed in: the session cookie is set
    assert settings.auth_cookie_name in r.cookies

    with tenant_bypass():
        company = db.get(Company, _uuid.UUID(body["company"]["id"]))
    assert company.kind == "personal"


def test_a_stranger_cannot_provision_a_business_tenant(client, db, signup_enabled):
    """Even if the payload asks for one — the endpoint doesn't offer the knob,
    and the service is called with kind='personal' unconditionally."""
    r = client.post("/auth/signup", json={
        "username": "sneaky", "password": PW, "kind": "business", "role": "owner"})
    assert r.status_code == 201
    with tenant_bypass():
        u = db.execute(select(User).where(User.username == "sneaky")).scalars().one()
        company = db.get(Company, u.company_id)
    assert u.role == "personal"
    assert u.is_admin is False
    assert company.kind == "personal"


def test_the_new_tenant_gets_the_personal_chart(client, db, signup_enabled):
    body = _signup(client, "charted").json()
    with use_company(body["company"]["id"]):
        codes = {a.code for a in db.execute(select(Account)).scalars().all()}
    assert "6110" in codes           # a personal spending category
    assert "2160" not in codes       # payroll tax payable — SME chart only


def test_the_display_name_is_used_when_given(client, db, signup_enabled):
    body = _signup(client, "named", display_name="پول من").json()
    assert body["company"]["name"] == "پول من"


def test_the_username_is_the_fallback_name(client, signup_enabled):
    assert _signup(client, "unnamed").json()["company"]["name"] == "unnamed"


def test_two_signups_are_isolated_from_each_other(client, db, signup_enabled):
    a = _signup(client, "alice").json()
    b = _signup(client, "bob").json()
    assert a["company"]["id"] != b["company"]["id"]
    with tenant_bypass():
        ua = db.execute(select(User).where(User.username == "alice")).scalars().one()
        ub = db.execute(select(User).where(User.username == "bob")).scalars().one()
    assert ua.company_id != ub.company_id


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------
def test_a_duplicate_username_is_rejected(client, signup_enabled):
    assert _signup(client, "taken").status_code == 201
    second = _signup(client, "taken")
    assert second.status_code == 400
    assert "exists" in second.json()["detail"].lower()


@pytest.mark.parametrize("weak", ["short", "12345678", "password"])
def test_a_weak_password_is_rejected(client, signup_enabled, weak):
    r = _signup(client, "weakling", password=weak)
    assert r.status_code == 400


def test_a_very_short_username_is_rejected(client, signup_enabled):
    assert _signup(client, "ab").status_code == 422


def test_an_unsupported_locale_is_rejected(client, signup_enabled):
    r = _signup(client, "wronglocale", locale="mars")
    assert r.status_code == 400


def test_signup_is_rate_limited(client, signup_enabled):
    """The abuse case is one actor minting many accounts, so the limit is per
    IP rather than per username."""
    codes = [_signup(client, f"flood{i}").status_code for i in range(7)]
    assert 429 in codes
    assert codes.count(201) <= 5


# ---------------------------------------------------------------------------
# The account actually works afterwards
# ---------------------------------------------------------------------------
def test_the_new_account_can_log_in(client, signup_enabled):
    _signup(client, "returning")
    client.cookies.clear()
    r = client.post("/auth/login", json={"username": "returning", "password": PW})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "personal"
