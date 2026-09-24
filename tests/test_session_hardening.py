"""Security review 2026-09-24, H3/H4: sessions must die with the user, the
password and on logout; changing a password needs the current one."""
from __future__ import annotations

import uuid

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token, hash_password
from app.core.config import settings
from app.models.user import User


def _user(db, password="Regular#Pass2026", **kw) -> User:
    h, s = hash_password(password)
    u = User(username=f"sess-{uuid.uuid4().hex[:8]}", password_hash=h, password_salt=s,
             is_admin=True, role="owner", is_active=True, **kw)
    db.add(u); db.commit()
    return u


def _login(client, u, password="Regular#Pass2026"):
    client.cookies.clear()
    csrf = generate_csrf_token()
    client.cookies.set(CSRF_COOKIE, csrf)
    r = client.post("/auth/login", json={"username": u.username, "password": password})
    assert r.status_code == 200, r.text
    return client.cookies.get(settings.auth_cookie_name), {"X-CSRF-Token": csrf}


def test_logout_invalidates_the_cookie_everywhere(client, db):
    u = _user(db)
    token, hdr = _login(client, u)
    assert client.get("/entities").status_code == 200
    assert client.post("/auth/logout", headers=hdr).status_code == 200
    # a saved copy of the cookie (another device, a stolen token) is dead too
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, token)
    assert client.get("/entities").status_code == 401
    db.refresh(u)
    assert u.token_version == 1


def test_change_password_needs_the_current_one_and_kills_other_sessions(client, db):
    u = _user(db)
    old_token, hdr = _login(client, u)
    bad = client.post("/auth/change-password", json={"current_password": "nope", "password": "Another#Pass2026"}, headers=hdr)
    assert bad.status_code == 400 and "Current password" in bad.json()["detail"]
    weak = client.post("/auth/change-password", json={"current_password": "Regular#Pass2026", "password": "12345678"}, headers=hdr)
    assert weak.status_code == 400  # all digits
    same = client.post("/auth/change-password", json={"current_password": "Regular#Pass2026", "password": "Regular#Pass2026"}, headers=hdr)
    assert same.status_code == 400
    ok = client.post("/auth/change-password", json={"current_password": "Regular#Pass2026", "password": "Another#Pass2026"}, headers=hdr)
    assert ok.status_code == 200, ok.text
    new_token = client.cookies.get(settings.auth_cookie_name)
    assert new_token and new_token != old_token
    assert client.get("/entities").status_code == 200  # this session continues
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, old_token)
    assert client.get("/entities").status_code == 401  # every other session is out
    # the new password works, the old one does not
    client.cookies.clear()
    client.cookies.set(CSRF_COOKIE, generate_csrf_token())
    assert client.post("/auth/login", json={"username": u.username, "password": "Regular#Pass2026"}).status_code == 401
    assert client.post("/auth/login", json={"username": u.username, "password": "Another#Pass2026"}).status_code == 200


def test_deleted_user_session_is_rejected_in_production(client, db, monkeypatch):
    u = _user(db)
    _login(client, u)
    assert client.get("/entities").status_code == 200
    db.delete(u); db.commit()
    monkeypatch.setattr(settings, "app_env", "prod")
    assert client.get("/entities").status_code == 401


def test_validation_errors_fail_closed(client, db, monkeypatch):
    import app.main as main_mod
    u = _user(db)
    _login(client, u)
    assert client.get("/entities").status_code == 200

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(main_mod, "_resolve_validation_session", boom)
    assert client.get("/entities").status_code == 401


def test_superadmin_flag_is_reread_from_the_database(client, db):
    u = _user(db)  # is_superadmin False in the DB
    forged = create_session_token(user_id=str(u.id), username=u.username, is_admin=True,
                                  is_superadmin=True, token_version=u.token_version)
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, forged)
    r = client.get("/admin/companies")
    assert r.status_code == 403
    assert client.get("/auth/me").json()["user"].get("is_superadmin") in (False, None)
