"""Review 2026-11-16.

* ``hmac.compare_digest`` raises TypeError on str with non-ASCII characters.
  The sign-in compared the typed password to the seeded default that way, so a
  correct Persian password ended in a 500; the CSRF and session-token checks and
  a two-factor code typed in Persian digits did the same.
* A fresh production server seeded ``admin`` / ``admin``: whoever reached it
  first chose the platform's password. In production the first password is now
  INITIAL_ADMIN_PASSWORD, or a random one written to the log.
"""
from __future__ import annotations

import logging
import uuid

from app.core.auth import hash_password
from app.models.company import Company
from app.models.user import User

PERSIAN_PW = "گذرواژه‌ی-خوب۱۴۰۵"


def _user_with(db, password: str) -> User:
    co = Company(id=uuid.uuid4(), name="Co fa", slug=f"fa-{uuid.uuid4().hex[:8]}", locale="ir",
                 base_currency="IRR", status="active", token_version=0)
    db.add(co)
    db.flush()
    h, s = hash_password(password)
    u = User(id=uuid.uuid4(), username=f"fa-{uuid.uuid4().hex[:6]}", password_hash=h, password_salt=s,
             preferred_language="fa", role="owner", is_admin=True, is_active=True, company_id=co.id,
             token_version=0)
    db.add(u)
    db.commit()
    return u


def test_a_persian_password_signs_in(client, db):
    u = _user_with(db, PERSIAN_PW)
    r = client.post("/auth/login", json={"username": u.username, "password": PERSIAN_PW})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and not r.json().get("must_change_password")
    wrong = client.post("/auth/login", json={"username": u.username, "password": PERSIAN_PW + "x"})
    assert wrong.status_code == 401


def test_non_ascii_csrf_or_session_values_are_refused_not_500():
    from types import SimpleNamespace

    from app.core.auth import CSRF_COOKIE, CSRF_HEADER, parse_session_token, validate_csrf
    assert parse_session_token("e30.ü-not-a-signature") is None

    def req(cookie, header):
        return SimpleNamespace(cookies={CSRF_COOKIE: cookie}, headers={CSRF_HEADER: header})
    assert validate_csrf(req("abc", "äbc")) is False
    assert validate_csrf(req("äbc", "äbc")) is True


def test_a_two_factor_code_in_persian_digits(monkeypatch):
    from app.core import two_factor
    secret = two_factor.new_secret() if hasattr(two_factor, "new_secret") else "JBSWY3DPEHPK3PXP"
    now = 1_800_000_000.0
    step = two_factor.current_step(now)
    ascii_code = two_factor.code_at(secret, step)
    persian = ascii_code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert two_factor.verify_totp(secret, persian, now=now) == step
    assert two_factor.verify_totp(secret, "²" * len(ascii_code), now=now) is None


def test_production_seeds_admin_without_the_default_password(monkeypatch, caplog):
    from app.core.auth import verify_password
    from app.core.config import settings
    from app.db.seed import initial_admin_password

    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    with caplog.at_level(logging.WARNING, logger="app.security"):
        generated = initial_admin_password()
    assert generated != "admin" and len(generated) >= 20
    assert generated in caplog.text                         # the operator finds it in the log, once

    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Chosen-by-0ps!")
    assert initial_admin_password() == "Chosen-by-0ps!"
    for weak in ("admin", "short1"):
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", weak)
        assert initial_admin_password() not in ("admin", "short1")

    monkeypatch.setattr(settings, "app_env", "dev")
    assert initial_admin_password() == "admin"              # dev/test keep the locked default
    h, s = hash_password(initial_admin_password())
    assert verify_password("admin", h, s)
