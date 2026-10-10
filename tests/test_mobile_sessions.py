"""The Android app's sessions (scenarios N1–N5, roadmap ROADMAP_ANDROID_CHAT
P0.1/P0.2): sign-in per device, bearer requests without CSRF, rotating
refresh tokens that revoke a device when an old one comes back, the device
list, and the minimum app version."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.auth import create_session_token, hash_password, parse_session_token
from app.core.config import settings
from app.models.company import Company
from app.models.mobile_device import MobileDevice
from app.models.user import User
from tests.test_two_factor import PASSWORD, _csrf, _enrol, _next_code, clock  # noqa: F401  (fixture)

API = "/api/mobile/v1"


def _company(db, status="active"):
    c = Company(id=uuid.uuid4(), name="Phone Co", slug=f"mob-{uuid.uuid4().hex[:8]}",
                locale="ir", base_currency="IRR", status=status, token_version=0)
    db.add(c)
    db.commit()
    return c


def _user(db, company, *, role="owner", password=PASSWORD):
    h, s = hash_password(password)
    u = User(username=f"mob-{uuid.uuid4().hex[:8]}", password_hash=h, password_salt=s,
             is_admin=(role == "owner"), role=role, is_active=True, company_id=company.id)
    db.add(u)
    db.commit()
    return u


def _sign_in(client, user, *, device="Pixel 8", password=PASSWORD, headers=None):
    client.cookies.clear()
    return client.post(f"{API}/auth/login", headers=headers or {},
                       json={"username": user.username, "password": password,
                             "device_name": device, "app_version": "0.1.0"})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _device(db, device_id):
    db.expire_all()
    return db.get(MobileDevice, uuid.UUID(device_id))


@pytest.fixture()
def owner(db):
    co = _company(db)
    return _user(db, co)


# --- N1: sign in -------------------------------------------------------------------------

def test_n1_a_phone_signs_in_with_a_device_and_two_tokens(client, db, owner):
    r = _sign_in(client, owner)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == settings.mobile_access_minutes * 60
    assert body["user"]["username"] == owner.username and body["company"]["base_currency"] == "IRR"
    session = parse_session_token(body["access_token"])
    assert session.device_id == body["device_id"] and session.user_id == str(owner.id)
    device = _device(db, body["device_id"])
    assert device.name == "Pixel 8" and device.app_version == "0.1.0" and device.revoked_at is None
    assert device.refresh_hash != body["refresh_token"]          # only its hash is kept
    assert client.cookies.get(settings.auth_cookie_name) is None  # no web cookie for a phone


def test_n1_two_factor_answers_with_a_challenge_then_the_tokens(client, db, owner, clock):
    secret, _codes = _enrol(client, db, owner, clock)
    r = _sign_in(client, owner)
    assert r.status_code == 200 and r.json()["two_factor_required"] is True and "access_token" not in r.json()
    r = client.post(f"{API}/auth/2fa", json={"challenge": r.json()["challenge"], "code": _next_code(clock, secret),
                                             "device_name": "Galaxy"})
    assert r.status_code == 200, r.text
    assert r.json()["two_factor_method"] == "totp" and _device(db, r.json()["device_id"]).name == "Galaxy"


def test_n1_the_default_password_and_a_suspended_company_are_refused(client, db):
    seeded = _user(db, _company(db), password="admin")
    r = _sign_in(client, seeded, password="admin")
    assert r.status_code == 403 and r.headers.get("X-Error-Code") == "password_change_required"
    suspended = _user(db, _company(db, status="suspended"))
    assert _sign_in(client, suspended).status_code == 403
    db.expire_all()
    assert db.query(MobileDevice).filter(MobileDevice.user_id.in_([seeded.id, suspended.id])).count() == 0


def test_n1_wrong_passwords_count_towards_the_same_limit_as_the_web(client, db, owner):
    for _ in range(5):
        assert _sign_in(client, owner, password="wrong-password").status_code == 401
    assert _sign_in(client, owner).status_code == 429
    assert client.post("/auth/login", json={"username": owner.username, "password": PASSWORD}).status_code == 429


# --- N2: bearer requests -------------------------------------------------------------------

def test_n2_a_bearer_is_served_without_csrf_and_a_cookie_still_needs_it(client, db, owner):
    access = _sign_in(client, owner).json()["access_token"]
    client.cookies.clear()
    me = client.get(f"{API}/me", headers=_bearer(access))
    assert me.status_code == 200 and me.json()["user"]["username"] == owner.username
    assert me.json()["device_id"] and me.json()["company"]["kind"] == "business"
    # a write, no CSRF header: fine with a bearer
    assert client.patch("/auth/preferences", json={"language": "fa"}, headers=_bearer(access)).status_code == 200
    # the same write on a web session without the CSRF header is refused
    client.post("/auth/login", json={"username": owner.username, "password": PASSWORD})
    assert client.patch("/auth/preferences", json={"language": "en"}).status_code == 403


def test_n2_a_web_token_is_not_a_bearer_and_an_expired_one_says_so(client, db, owner):
    web = create_session_token(user_id=str(owner.id), username=owner.username, is_admin=True,
                               company_id=str(owner.company_id), role="owner")
    r = client.get(f"{API}/me", headers=_bearer(web))
    assert r.status_code == 401 and r.json()["code"] == "session_expired"
    device_id = _sign_in(client, owner).json()["device_id"]
    client.cookies.clear()
    stale = create_session_token(user_id=str(owner.id), username=owner.username, is_admin=True,
                                 company_id=str(owner.company_id), role="owner",
                                 ttl_seconds=-1, device_id=device_id)
    r = client.get(f"{API}/me", headers=_bearer(stale))
    assert r.status_code == 401 and r.json()["code"] == "session_expired"


def test_n2_the_bearer_reaches_the_business_routes_with_the_users_role(client, db):
    co = _company(db)
    viewer = _user(db, co, role="viewer")
    access = _sign_in(client, viewer).json()["access_token"]
    client.cookies.clear()
    assert client.get(f"{API}/devices", headers=_bearer(access)).status_code == 200
    assert client.get("/accounts", headers=_bearer(access)).status_code == 200
    r = client.post("/transactions", headers=_bearer(access), json={"date": "2026-10-01", "lines": []})
    assert r.status_code == 403                                   # a viewer still can't post


# --- N3: refresh -------------------------------------------------------------------------

def test_n3_refresh_rotates_and_an_old_refresh_token_revokes_the_device(client, db, owner):
    first = _sign_in(client, owner).json()
    client.cookies.clear()
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"] and second["device_id"] == first["device_id"]
    assert client.get(f"{API}/me", headers=_bearer(second["access_token"])).status_code == 200
    # the old refresh token again: someone holds a copy
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 401 and r.headers.get("X-Error-Code") == "session_ended"
    assert _device(db, first["device_id"]).revoked_at is not None
    assert client.get(f"{API}/me", headers=_bearer(second["access_token"])).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_n3_an_expired_or_unknown_refresh_token_is_refused(client, db, owner):
    body = _sign_in(client, owner).json()
    client.cookies.clear()
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": "x" * 40}).status_code == 401
    device = _device(db, body["device_id"])
    device.refresh_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401


# --- N4: devices -------------------------------------------------------------------------

def test_n4_list_revoke_and_sign_out(client, db, owner):
    a = _sign_in(client, owner, device="Pixel 8").json()
    b = _sign_in(client, owner, device="Old Galaxy").json()
    client.cookies.clear()
    rows = client.get(f"{API}/devices", headers=_bearer(a["access_token"])).json()
    assert {d["name"] for d in rows} == {"Pixel 8", "Old Galaxy"}
    assert [d["name"] for d in rows if d["this_device"]] == ["Pixel 8"]
    # revoke the old phone from the new one
    assert client.delete(f"{API}/devices/{b['device_id']}", headers=_bearer(a["access_token"])).status_code == 200
    assert client.get(f"{API}/me", headers=_bearer(b["access_token"])).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": b["refresh_token"]}).status_code == 401
    assert client.delete(f"{API}/devices/{b['device_id']}", headers=_bearer(a["access_token"])).status_code == 404
    # sign this phone out: only this phone
    c = _sign_in(client, owner, device="Tablet").json()
    client.cookies.clear()
    assert client.delete(f"{API}/session", headers=_bearer(a["access_token"])).status_code == 200
    assert client.get(f"{API}/me", headers=_bearer(a["access_token"])).status_code == 401
    assert client.get(f"{API}/me", headers=_bearer(c["access_token"])).status_code == 200


def test_n4_a_password_change_ends_every_phone(client, db, owner):
    phone = _sign_in(client, owner).json()
    client.cookies.clear()
    assert client.post("/auth/login", json={"username": owner.username, "password": PASSWORD}).status_code == 200
    r = client.post("/auth/change-password", headers=_csrf(client),
                    json={"current_password": PASSWORD, "password": "An0ther#Strong-One"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    assert client.get(f"{API}/me", headers=_bearer(phone["access_token"])).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 401


def test_n4_no_one_revokes_another_users_phone(client, db, owner):
    other = _user(db, _company(db))
    theirs = _sign_in(client, other).json()
    mine = _sign_in(client, owner).json()
    client.cookies.clear()
    r = client.delete(f"{API}/devices/{theirs['device_id']}", headers=_bearer(mine["access_token"]))
    assert r.status_code == 404
    assert client.get(f"{API}/me", headers=_bearer(theirs["access_token"])).status_code == 200


# --- N5: the minimum app version --------------------------------------------------------------

def test_n5_an_app_below_the_minimum_is_asked_to_update(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "mobile_min_app_version", "0.2.0")
    r = _sign_in(client, owner, headers={"X-App-Version": "0.1.9"})
    assert r.status_code == 426 and r.json() == {
        "code": "upgrade_required", "min_version": "0.2.0",
        "detail": "This version of the app is too old. Update it to keep going."}
    assert _sign_in(client, owner, headers={"X-App-Version": "0.2"}).status_code == 200
    assert _sign_in(client, owner).status_code == 200            # a client that doesn't say is served
