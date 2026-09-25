"""Two-factor sign-in with an authenticator app (roadmap 2026-09 §1.13).

The TOTP maths against the RFC 6238 vectors; recovery codes; the signed
challenge between the password and the code; and the HTTP flows — set-up,
sign-in, replay, rate limit, turn-off, new recovery codes, owner reset.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.core import two_factor as tf
from app.core.auth import CSRF_COOKIE, generate_csrf_token, hash_password, parse_session_token
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.user import User

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # b"12345678901234567890"
PASSWORD = "Str0ng#Passw0rd!"
T0 = 1_900_000_000.0  # a fixed "now" for the HTTP flows


# --- pure TOTP -------------------------------------------------------------------

@pytest.mark.parametrize("t, code", [
    (59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
    (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130"),
])
def test_rfc6238_sha1_vectors(t, code):
    # RFC 6238 appendix B, last six of the eight digits
    assert tf.code_at(RFC_SECRET, tf.current_step(t)) == code


def test_secret_is_160_bits_base32():
    s = tf.new_secret()
    assert len(tf._key(s)) == 20
    assert s == s.upper() and "=" not in s
    assert tf.new_secret() != s


def test_verify_accepts_one_step_of_drift_either_side_only():
    now = 1_700_000_000.0
    step = tf.current_step(now)
    for d in (-1, 0, 1):
        assert tf.verify_totp(RFC_SECRET, tf.code_at(RFC_SECRET, step + d), now=now) == step + d
    for d in (-2, 2):
        assert tf.verify_totp(RFC_SECRET, tf.code_at(RFC_SECRET, step + d), now=now) is None


def test_verify_refuses_a_used_step_and_says_so():
    now = 1_700_000_000.0
    step = tf.current_step(now)
    code = tf.code_at(RFC_SECRET, step)
    assert tf.verify_totp(RFC_SECRET, code, last_step=step, now=now) is None
    assert tf.was_already_used(RFC_SECRET, code, last_step=step, now=now)
    other = next(c for c in ("000000", "111111") if c not in {tf.code_at(RFC_SECRET, step + d) for d in (-1, 0, 1)})
    assert not tf.was_already_used(RFC_SECRET, other, last_step=step, now=now)
    assert not tf.was_already_used(RFC_SECRET, code, last_step=None, now=now)
    # the next step's code is still good
    nxt = tf.code_at(RFC_SECRET, step + 1)
    assert tf.verify_totp(RFC_SECRET, nxt, last_step=step, now=now + 30) == step + 1


@pytest.mark.parametrize("typed", ["{c}", " {c} ", "{a} {b}", "{a}-{b}", "{fa}", "{ar}"])
def test_codes_are_normalised(typed):
    now = 1_700_000_000.0
    c = tf.code_at(RFC_SECRET, tf.current_step(now))
    fa = c.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    ar = c.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    entry = typed.format(c=c, a=c[:3], b=c[3:], fa=fa, ar=ar)
    assert tf.verify_totp(RFC_SECRET, entry, now=now) is not None


@pytest.mark.parametrize("bad", [None, "", "12345", "1234567", "abcdef", "12 34 5"])
def test_malformed_codes_are_refused(bad):
    assert tf.verify_totp(RFC_SECRET, bad, now=1_700_000_000.0) is None


def test_provisioning_uri_and_qr():
    uri = tf.provisioning_uri("ABCDEF", "ali@acme")
    assert uri.startswith("otpauth://totp/Accounting%20Assistant%3Aali%40acme?")
    assert "secret=ABCDEF" in uri and "issuer=Accounting%20Assistant" in uri
    assert "digits=6" in uri and "period=30" in uri
    svg = tf.qr_svg(uri)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "ABCDEF" not in svg  # the secret is only in the modules, never as text
    assert len(svg) < 60_000


def test_qr_refuses_text_that_cannot_fit():
    with pytest.raises(ValueError):
        tf.qr_svg("x" * 5000)


# --- recovery codes ------------------------------------------------------------------

def test_recovery_codes_are_single_use_and_stored_hashed():
    codes, stored = tf.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    for c in codes:
        assert c not in stored and c.replace("-", "") not in stored
    assert tf.recovery_codes_left(stored) == 10
    after = tf.use_recovery_code(stored, codes[3].upper().replace("-", " "))
    assert after is not None and tf.recovery_codes_left(after) == 9
    assert tf.use_recovery_code(after, codes[3]) is None  # used up
    assert tf.use_recovery_code(after, "aaaaa-aaaaa") is None
    assert tf.use_recovery_code(after, "short") is None
    assert tf.use_recovery_code("not json", codes[0]) is None
    assert tf.recovery_codes_left("not json") == 0
    assert tf.recovery_codes_left(None) == 0


# --- challenge ---------------------------------------------------------------------------

def test_challenge_roundtrip_expiry_and_tamper():
    tok = tf.issue_challenge(user_id="u1", token_version=3, must_change_password=True, now=1000)
    data = tf.parse_challenge(tok, now=1100)
    assert data["uid"] == "u1" and data["tv"] == 3 and data["pwc"] is True
    assert tf.parse_challenge(tok, now=1000 + tf.CHALLENGE_SECONDS) is None
    head, body, sig = tok.split(".")
    assert tf.parse_challenge(f"{head}.{body}.{'0' * len(sig)}", now=1100) is None
    assert tf.parse_challenge(f"{head}.{body}x.{sig}", now=1100) is None
    assert tf.parse_challenge(None) is None
    assert tf.parse_challenge("garbage") is None


def test_challenge_is_never_a_session_and_vice_versa():
    from app.core.auth import create_session_token
    tok = tf.issue_challenge(user_id=str(uuid.uuid4()), token_version=0)
    assert parse_session_token(tok) is None
    assert parse_session_token(tok.split(".", 1)[1]) is None
    session = create_session_token(user_id=str(uuid.uuid4()), username="x", is_admin=True)
    assert tf.parse_challenge(session) is None
    assert tf.parse_challenge("2fa." + session) is None


# --- HTTP helpers --------------------------------------------------------------------------

@pytest.fixture()
def clock(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(tf, "_clock", lambda: state["now"])
    return state


def _user(db, *, role="owner", company=None, password=PASSWORD, username=None):
    h, s = hash_password(password)
    u = User(username=username or f"tfa-{uuid.uuid4().hex[:8]}", password_hash=h, password_salt=s,
             is_admin=(role == "owner"), role=role, is_active=True,
             company_id=company.id if company else None)
    db.add(u)
    db.commit()
    return u


def _company(db, status="active"):
    c = Company(id=uuid.uuid4(), name="2FA Ltd", slug=f"tfa-{uuid.uuid4().hex[:8]}",
                locale="uk", base_currency="GBP", status=status, token_version=0)
    db.add(c)
    db.commit()
    return c


def _csrf(client):
    tok = generate_csrf_token()
    client.cookies.set(CSRF_COOKIE, tok)
    return {"X-CSRF-Token": tok}


def _login(client, user, password=PASSWORD):
    return client.post("/auth/login", json={"username": user.username, "password": password})


def _session(client):
    return client.cookies.get(settings.auth_cookie_name)


def _enrol(client, db, user, clock):
    """Password login, set-up, confirm. Returns (secret, recovery codes)."""
    assert _login(client, user).status_code == 200
    hdr = _csrf(client)
    r = client.post("/auth/2fa/setup", json={"password": PASSWORD}, headers=hdr)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    code = tf.code_at(secret, tf.current_step())
    r = client.post("/auth/2fa/enable", json={"code": code}, headers=hdr)
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _next_code(clock, secret):
    clock["now"] += 30
    return tf.code_at(secret, tf.current_step())


def _signin_with_code(client, user, code):
    client.cookies.clear()
    r = _login(client, user)
    assert r.status_code == 200 and r.json()["two_factor_required"] is True
    return client.post("/auth/login/2fa", json={"challenge": r.json()["challenge"], "code": code})


# --- set-up ----------------------------------------------------------------------------------

def test_setup_and_enable_flow(client, db, clock):
    user = _user(db)
    assert _login(client, user).status_code == 200
    old_session = _session(client)
    hdr = _csrf(client)
    assert client.get("/auth/2fa").json() == {"enabled": False, "enabled_at": None, "pending": False,
                                              "recovery_codes_left": 0}
    assert client.post("/auth/2fa/setup", json={"password": "wrong"}, headers=hdr).status_code == 400
    r = client.post("/auth/2fa/setup", json={"password": PASSWORD}, headers=hdr)
    assert r.status_code == 200
    body = r.json()
    secret = body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://totp/") and f"secret={secret}" in body["otpauth_uri"]
    assert body["qr_svg"].startswith("<svg")
    db.refresh(user)
    assert user.totp_pending_secret.startswith("enc:v1:") and secret not in user.totp_pending_secret
    assert user.totp_secret is None and user.totp_enabled_at is None
    assert client.get("/auth/2fa").json()["pending"] is True

    # A pending set-up changes nothing about sign-in yet.
    assert client.post("/auth/2fa/enable", json={"code": "000000" if tf.code_at(secret, tf.current_step()) != "000000" else "111111"},
                       headers=hdr).status_code == 400
    r = client.post("/auth/2fa/enable", json={"code": tf.code_at(secret, tf.current_step())}, headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["recovery_codes_left"] == 10
    assert len(body["recovery_codes"]) == 10
    db.refresh(user)
    assert user.totp_secret.startswith("enc:v1:") and user.totp_pending_secret is None
    assert user.totp_last_step == tf.current_step()
    assert all(c not in (user.totp_recovery or "") for c in body["recovery_codes"])

    # Every other session ended; this one was re-issued.
    assert _session(client) != old_session
    assert client.get("/auth/me").status_code == 200
    client.cookies.set(settings.auth_cookie_name, old_session)
    assert client.get("/auth/me").status_code == 401

    audit = db.execute(select(AuditLog).where(AuditLog.action == "2fa_enabled",
                                              AuditLog.entity_id == str(user.id))).scalars().first()
    assert audit is not None


def test_setup_refused_when_already_on_and_enable_needs_setup_first(client, db, clock):
    user = _user(db)
    assert _login(client, user).status_code == 200
    hdr = _csrf(client)
    assert client.post("/auth/2fa/enable", json={"code": "123456"}, headers=hdr).status_code == 400
    _enrol(client, db, user, clock)
    hdr = _csrf(client)
    assert client.post("/auth/2fa/setup", json={"password": PASSWORD}, headers=hdr).status_code == 409
    assert client.post("/auth/2fa/enable", json={"code": "123456"}, headers=hdr).status_code == 409


def test_self_service_endpoints_need_a_session_and_csrf(client, db):
    assert client.get("/auth/2fa").status_code == 401
    user = _user(db)
    assert _login(client, user).status_code == 200
    assert client.post("/auth/2fa/setup", json={"password": PASSWORD}).status_code == 403  # no CSRF header


def test_wrong_passwords_at_setup_count_towards_the_login_lockout(client, db):
    user = _user(db)
    assert _login(client, user).status_code == 200
    hdr = _csrf(client)
    for _ in range(5):
        assert client.post("/auth/2fa/setup", json={"password": "nope"}, headers=hdr).status_code == 400
    assert client.post("/auth/2fa/setup", json={"password": PASSWORD}, headers=hdr).status_code == 429


# --- sign-in ----------------------------------------------------------------------------------

def test_password_alone_no_longer_opens_a_session(client, db, clock):
    user = _user(db)
    _enrol(client, db, user, clock)
    client.cookies.clear()
    r = _login(client, user)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["two_factor_required"] is True
    assert body["challenge"].startswith("2fa.")
    assert "user" not in body
    assert _session(client) is None
    assert client.get("/auth/me").status_code == 401
    # the challenge is not a session even if planted as one
    client.cookies.set(settings.auth_cookie_name, body["challenge"])
    assert client.get("/auth/me").status_code == 401


def test_code_completes_the_sign_in_and_is_audited(client, db, clock):
    user = _user(db)
    secret, _ = _enrol(client, db, user, clock)
    r = _signin_with_code(client, user, _next_code(clock, secret))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["two_factor_method"] == "totp"
    assert body["user"]["username"] == user.username
    me = client.get("/auth/me").json()
    assert me["user"]["two_factor_enabled"] is True
    # committed, not just flushed: survives the request session's rollback
    db.rollback()
    row = db.execute(select(AuditLog).where(AuditLog.action == "login", AuditLog.entity_id == str(user.id))
                     .order_by(AuditLog.timestamp.desc())).scalars().first()
    assert row is not None and "authenticator" in (row.detail or "")


def test_the_same_code_cannot_be_used_twice(client, db, clock):
    user = _user(db)
    secret, _ = _enrol(client, db, user, clock)
    code = _next_code(clock, secret)
    assert _signin_with_code(client, user, code).status_code == 200
    r = _signin_with_code(client, user, code)
    assert r.status_code == 401
    assert "already used" in r.json()["detail"]
    assert _session(client) is None


def test_wrong_code_is_refused_and_rate_limited(client, db, clock):
    user = _user(db)
    secret, _ = _enrol(client, db, user, clock)
    client.cookies.clear()
    challenge = _login(client, user).json()["challenge"]
    good = tf.code_at(secret, tf.current_step() + 1)
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        r = client.post("/auth/login/2fa", json={"challenge": challenge, "code": wrong})
        assert r.status_code == 401
    # locked for this user now, even with the right code
    r = client.post("/auth/login/2fa", json={"challenge": challenge, "code": good})
    assert r.status_code == 429
    failed = db.execute(select(AuditLog).where(AuditLog.action == "login_2fa_failed",
                                               AuditLog.entity_id == str(user.id))).scalars().all()
    assert len(failed) == 5


def test_recovery_code_signs_in_once(client, db, clock):
    user = _user(db)
    _, codes = _enrol(client, db, user, clock)
    r = _signin_with_code(client, user, codes[0])
    assert r.status_code == 200, r.text
    assert r.json()["two_factor_method"] == "recovery" and r.json()["recovery_codes_left"] == 9
    assert _signin_with_code(client, user, codes[0]).status_code == 401
    assert _signin_with_code(client, user, codes[1].upper()).status_code == 200


def test_challenge_expires_and_dies_with_a_password_change(client, db, clock):
    user = _user(db)
    secret, _ = _enrol(client, db, user, clock)
    client.cookies.clear()
    challenge = _login(client, user).json()["challenge"]
    clock["now"] += tf.CHALLENGE_SECONDS + 1
    r = client.post("/auth/login/2fa", json={"challenge": challenge,
                                             "code": tf.code_at(secret, tf.current_step())})
    assert r.status_code == 401 and "timed out" in r.json()["detail"]

    challenge = _login(client, user).json()["challenge"]
    db.refresh(user)
    user.token_version = int(user.token_version or 0) + 1  # e.g. an owner reset the password
    db.commit()
    r = client.post("/auth/login/2fa", json={"challenge": challenge, "code": _next_code(clock, secret)})
    assert r.status_code == 401


def test_bad_challenges_are_refused(client, db):
    for ch in ("nope", "2fa.x.y", tf.issue_challenge(user_id="not-a-uuid", token_version=0),
               tf.issue_challenge(user_id=str(uuid.uuid4()), token_version=0)):
        r = client.post("/auth/login/2fa", json={"challenge": ch, "code": "123456"})
        assert r.status_code == 401, ch


def test_suspended_company_is_refused_at_the_code_step(client, db, clock):
    co = _company(db)
    user = _user(db, company=co)
    secret, _ = _enrol(client, db, user, clock)
    client.cookies.clear()
    challenge = _login(client, user).json()["challenge"]
    co.status = "suspended"
    db.commit()
    r = client.post("/auth/login/2fa", json={"challenge": challenge, "code": _next_code(clock, secret)})
    assert r.status_code == 403


def test_default_password_lock_survives_the_code_step(client, db, clock):
    user = _user(db, password="admin")
    assert _login(client, user, "admin").status_code == 200
    # enrol with the locked session is impossible — the lock only allows a password change
    hdr = _csrf(client)
    assert client.post("/auth/2fa/setup", json={"password": "admin"}, headers=hdr).status_code == 403
    # so put 2FA on directly, then sign in with the default password again
    secret = tf.new_secret()
    from app.core.secrets import encrypt_secret
    from datetime import datetime, timezone
    user.totp_secret = encrypt_secret(secret)
    user.totp_enabled_at = datetime.now(timezone.utc)
    db.commit()
    client.cookies.clear()
    r = _login(client, user, "admin")
    assert r.json()["two_factor_required"] is True
    r = client.post("/auth/login/2fa", json={"challenge": r.json()["challenge"],
                                             "code": tf.code_at(secret, tf.current_step())})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.get("/entities").status_code == 403


def test_cross_site_code_post_is_refused(client, db):
    r = client.post("/auth/login/2fa", json={"challenge": "x", "code": "123456"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_password_login_without_2fa_still_opens_a_session_and_is_audited(client, db):
    user = _user(db)
    r = _login(client, user)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert _session(client)
    db.rollback()
    assert db.execute(select(AuditLog).where(AuditLog.action == "login",
                                             AuditLog.entity_id == str(user.id))).scalars().first() is not None


# --- turning it off / new codes ---------------------------------------------------------------------

def test_disable_needs_password_and_code(client, db, clock):
    user = _user(db)
    secret, codes = _enrol(client, db, user, clock)
    hdr = _csrf(client)
    code = _next_code(clock, secret)
    assert client.post("/auth/2fa/disable", json={"password": "wrong", "code": code}, headers=hdr).status_code == 400
    assert client.post("/auth/2fa/disable", json={"password": PASSWORD, "code": "000000" if code != "000000" else "111111"},
                       headers=hdr).status_code == 400
    r = client.post("/auth/2fa/disable", json={"password": PASSWORD, "code": code}, headers=hdr)
    assert r.status_code == 200 and r.json()["enabled"] is False
    db.refresh(user)
    assert user.totp_secret is None and user.totp_recovery is None and user.totp_last_step is None
    # the session was re-issued and still works; password alone signs in again
    assert client.get("/auth/me").json()["user"]["two_factor_enabled"] is False
    client.cookies.clear()
    assert _login(client, user).json()["ok"] is True
    assert db.execute(select(AuditLog).where(AuditLog.action == "2fa_disabled",
                                             AuditLog.entity_id == str(user.id))).scalars().first() is not None


def test_disable_with_a_recovery_code(client, db, clock):
    user = _user(db)
    _, codes = _enrol(client, db, user, clock)
    hdr = _csrf(client)
    r = client.post("/auth/2fa/disable", json={"password": PASSWORD, "code": codes[5]}, headers=hdr)
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_disable_when_off_is_400(client, db):
    user = _user(db)
    assert _login(client, user).status_code == 200
    hdr = _csrf(client)
    assert client.post("/auth/2fa/disable", json={"password": PASSWORD, "code": "123456"}, headers=hdr).status_code == 400
    assert client.post("/auth/2fa/recovery-codes", json={"code": "123456"}, headers=hdr).status_code == 400


def test_new_recovery_codes_replace_the_old_and_need_an_app_code(client, db, clock):
    user = _user(db)
    secret, old = _enrol(client, db, user, clock)
    hdr = _csrf(client)
    assert client.post("/auth/2fa/recovery-codes", json={"code": old[0]}, headers=hdr).status_code == 400
    r = client.post("/auth/2fa/recovery-codes", json={"code": _next_code(clock, secret)}, headers=hdr)
    assert r.status_code == 200
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    assert _signin_with_code(client, user, old[1]).status_code == 401
    assert _signin_with_code(client, user, new[0]).status_code == 200


# --- /auth/me and the owner's reset -----------------------------------------------------------------

def test_me_recommends_2fa_to_owners_only(client, db):
    co = _company(db)
    owner = _user(db, company=co)
    accountant = _user(db, company=co, role="accountant")
    assert _login(client, owner).status_code == 200
    me = client.get("/auth/me").json()["user"]
    assert me["two_factor_enabled"] is False and me["two_factor_recommended"] is True
    client.cookies.clear()
    assert _login(client, accountant).status_code == 200
    assert client.get("/auth/me").json()["user"]["two_factor_recommended"] is False


def _as(client, user):
    client.cookies.clear()
    assert _login(client, user).status_code == 200
    return _csrf(client)


def test_owner_resets_a_colleagues_2fa(client, db, clock):
    co = _company(db)
    owner = _user(db, company=co)
    staff = _user(db, company=co, role="accountant")
    _enrol(client, db, staff, clock)
    db.refresh(staff)
    tv = staff.token_version

    hdr = _as(client, owner)
    listed = {u["username"]: u for u in client.get("/admin/users").json()}
    assert listed[staff.username]["two_factor"] is True
    assert listed[owner.username]["two_factor"] is False

    r = client.post(f"/admin/users/{staff.id}/reset-2fa", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["two_factor"] is False
    db.refresh(staff)
    assert staff.totp_secret is None and staff.totp_enabled_at is None
    assert staff.token_version == tv + 1
    row = db.execute(select(AuditLog).where(AuditLog.action == "2fa_reset",
                                            AuditLog.entity_id == str(staff.id))).scalars().first()
    assert row is not None and json.loads(row.detail)["username"] == staff.username
    # the colleague signs in with the password alone again
    client.cookies.clear()
    assert _login(client, staff).json()["ok"] is True
    # and a second reset has nothing to do
    hdr = _as(client, owner)
    assert client.post(f"/admin/users/{staff.id}/reset-2fa", headers=hdr).status_code == 400


def test_reset_refuses_self_other_companies_and_non_owners(client, db, clock):
    co, other = _company(db), _company(db)
    owner = _user(db, company=co)
    cfo = _user(db, company=co, role="cfo")
    staff = _user(db, company=co, role="accountant")
    outsider = _user(db, company=other)
    for u in (staff, outsider):
        _enrol(client, db, u, clock)

    hdr = _as(client, owner)
    # yourself: through Settings with a code, never the admin shortcut
    assert client.post(f"/admin/users/{owner.id}/reset-2fa", headers=hdr).status_code == 400
    # another company's user looks like no user at all
    assert client.post(f"/admin/users/{outsider.id}/reset-2fa", headers=hdr).status_code == 404
    hdr = _as(client, cfo)
    assert client.post(f"/admin/users/{staff.id}/reset-2fa", headers=hdr).status_code == 403
    db.refresh(staff)
    db.refresh(outsider)
    assert staff.totp_enabled_at is not None and outsider.totp_enabled_at is not None


def test_login_page_has_the_code_step():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "app" / "static" / "login.html").read_text(encoding="utf-8")
    assert 'id="tfa-form"' in html and "/auth/login/2fa" in html
    assert "passwordInput" not in html  # the undefined name that broke the default-password hand-off
