"""A login with the seeded default password ('admin') gets a session that can
only change the password (production QA 2026-09-24: admin/admin was live and
the login page advertised it)."""
from __future__ import annotations

import uuid
from pathlib import Path

from app.core.auth import hash_password
from app.models.user import User

LOGIN_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "login.html"


def _user(db, password: str) -> User:
    h, s = hash_password(password)
    u = User(username=f"dflt-{uuid.uuid4().hex[:8]}", password_hash=h, password_salt=s,
             is_admin=True, role="owner", is_active=True)
    db.add(u)
    db.commit()
    return u


def _csrf(client):
    from app.core.auth import CSRF_COOKIE
    return client.cookies.get(CSRF_COOKIE) or ""


def test_login_page_no_longer_advertises_the_default_credentials():
    html = LOGIN_HTML.read_text(encoding="utf-8")
    assert "admin / admin" not in html and "defaultAdmin" not in html
    assert 'id="change-form"' in html


def test_default_password_session_is_locked_until_changed(client, db):
    from app.core.auth import CSRF_COOKIE, generate_csrf_token

    u = _user(db, "admin")
    csrf = generate_csrf_token()
    client.cookies.set(CSRF_COOKIE, csrf)
    r = client.post("/auth/login", json={"username": u.username, "password": "admin"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True

    # Locked: the app shell redirects, API calls are refused with a reason.
    assert client.get("/", follow_redirects=False).status_code == 302
    r = client.get("/entities")
    assert r.status_code == 403 and r.json()["code"] == "password_change_required"
    r = client.post("/transactions", json={"date": "2026-01-01", "lines": []}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    # …but the change screen's own calls still work.
    assert client.get("/auth/me").status_code == 200
    assert client.get("/login").status_code == 200

    # Weak or default replacements are refused.
    hdr = {"X-CSRF-Token": csrf}
    cur = {"current_password": "admin"}
    assert client.post("/auth/change-password", json={**cur, "password": "admin"}, headers=hdr).status_code == 400
    assert client.post("/auth/change-password", json={**cur, "password": "short"}, headers=hdr).status_code == 400
    assert client.post("/auth/change-password", json={**cur, "password": u.username}, headers=hdr).status_code == 400
    # …and never without proof of the current password.
    assert client.post("/auth/change-password", json={"password": "Strong#Pass2026"}, headers=hdr).status_code == 422
    assert client.post("/auth/change-password", json={"current_password": "wrong", "password": "Strong#Pass2026"}, headers=hdr).status_code == 400

    # A real password unlocks the session in place (fresh cookie, no flag).
    r = client.post("/auth/change-password", json={**cur, "password": "Strong#Pass2026"}, headers=hdr)
    assert r.status_code == 200, r.text
    assert client.get("/entities").status_code == 200
    me = client.get("/auth/me").json()
    assert me["user"]["username"] == u.username

    # The next login with the new password is a normal session; the old one fails.
    client.cookies.clear()
    client.cookies.set(CSRF_COOKIE, csrf)
    assert client.post("/auth/login", json={"username": u.username, "password": "admin"}).status_code == 401
    r = client.post("/auth/login", json={"username": u.username, "password": "Strong#Pass2026"})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert client.get("/entities").status_code == 200


def test_normal_password_login_is_not_flagged(client, db):
    from app.core.auth import CSRF_COOKIE, generate_csrf_token
    u = _user(db, "Regular#Pass2026")
    client.cookies.set(CSRF_COOKIE, generate_csrf_token())
    r = client.post("/auth/login", json={"username": u.username, "password": "Regular#Pass2026"})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert client.get("/entities").status_code == 200


def test_token_round_trips_the_lock_flag():
    from app.core.auth import create_session_token, parse_session_token
    tok = create_session_token(user_id="u1", username="x", is_admin=True, must_change_password=True)
    assert parse_session_token(tok).must_change_password is True
    tok2 = create_session_token(user_id="u1", username="x", is_admin=True)
    assert parse_session_token(tok2).must_change_password is False


def test_a_fresh_browser_can_set_its_first_password(client, db):
    """No anti-forgery cookie beforehand, as in a new browser: the sign-in page
    sets it, and the change form's request carries it. The test above set the
    cookie by hand, so it never saw that a locked session — sent to the sign-in
    page before the app page that set the cookie — could never change its
    password (production, 2026-10-13)."""
    from app.core.auth import CSRF_COOKIE

    u = _user(db, "admin")
    client.cookies.clear()
    assert client.get("/login").status_code == 200
    token = client.cookies.get(CSRF_COOKIE)
    assert token                                              # set with the page
    r = client.post("/auth/login", json={"username": u.username, "password": "admin"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.get("/", follow_redirects=False).headers["location"] == "/login?change=1"
    assert client.get("/login?change=1").status_code == 200
    assert client.cookies.get(CSRF_COOKIE) == token           # kept, not replaced
    r = client.post("/auth/change-password", json={"current_password": "admin", "password": "Strong#Pass2026"},
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 200, r.text
    assert client.get("/entities").status_code == 200
    # still refused without the header: the protection is unchanged
    client.cookies.clear()
    r = client.post("/auth/login", json={"username": u.username, "password": "Strong#Pass2026"})
    assert client.post("/auth/change-password", json={"current_password": "Strong#Pass2026",
                                                     "password": "Another#Pass2026"}).status_code == 403


def test_the_change_form_sends_the_anti_forgery_header():
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "login.js").read_text(encoding="utf-8")
    start = js.index("fetch('/auth/change-password'")
    assert "csrfHeader()" in js[start:start + 300]
