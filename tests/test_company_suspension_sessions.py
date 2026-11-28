"""A suspension ends a company's sessions for good (review 2026-11-28,
scenario CV2).

Suspending a company bumped ``Company.token_version``, which nothing read:
the sessions were refused only by the status check, so reactivating the
company brought every old web cookie and signed-in phone back to life."""
from __future__ import annotations

import uuid

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.models.mobile_device import MobileDevice
from app.models.user import User
from tests.conftest import _CSRFTestClient
from tests.test_mobile_sessions import API, _bearer, _company, _sign_in, _user


def _cookie(user) -> str:
    return create_session_token(user_id=str(user.id), username=user.username, is_admin=True, role=user.role,
                                company_id=str(user.company_id), token_version=int(user.token_version or 0))


def _web_me(client, token) -> int:
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, token)
    return client.get("/auth/me").status_code


def _set_status(client, company_id, status) -> None:
    tok = create_session_token(user_id=str(uuid.uuid4()), username="superadmin", is_admin=True, is_superadmin=True)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    r = _CSRFTestClient(client, csrf).patch(f"/admin/companies/{company_id}", json={"status": status})
    assert r.status_code == 200, r.text
    client.cookies.clear()


def test_cv2_reactivating_a_company_does_not_revive_its_sessions(client, db):
    """CV2: web cookie, phone access token and phone refresh token all end on
    suspension and stay ended after reactivation; a new sign-in works, and
    another company's sessions are untouched."""
    co, other_co = _company(db), _company(db)
    owner, other = _user(db, co), _user(db, other_co)
    web, other_web = _cookie(owner), _cookie(other)
    phone = _sign_in(client, owner).json()
    other_phone = _sign_in(client, other).json()
    assert _web_me(client, web) == 200 and _web_me(client, other_web) == 200

    _set_status(client, co.id, "suspended")
    _set_status(client, co.id, "active")

    assert _web_me(client, web) == 401
    client.cookies.clear()
    assert client.get(f"{API}/me", headers=_bearer(phone["access_token"])).status_code == 401
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": phone["refresh_token"]})
    assert r.status_code == 401
    db.expire_all()
    assert db.get(MobileDevice, uuid.UUID(phone["device_id"])).revoked_at is not None

    # the other company: nothing changed
    assert _web_me(client, other_web) == 200
    client.cookies.clear()
    assert client.get(f"{API}/me", headers=_bearer(other_phone["access_token"])).status_code == 200
    assert db.get(MobileDevice, uuid.UUID(other_phone["device_id"])).revoked_at is None

    # a fresh sign-in after reactivation works
    db.expire_all()
    fresh = _sign_in(client, db.get(User, owner.id))
    assert fresh.status_code == 200, fresh.text
    client.cookies.clear()
    assert client.get(f"{API}/me", headers=_bearer(fresh.json()["access_token"])).status_code == 200


def test_cv2_a_patch_that_keeps_the_status_ends_no_session(client, db):
    """CV2: only the change to suspended ends sessions; renaming does not."""
    co = _company(db)
    owner = _user(db, co)
    web = _cookie(owner)
    tok = create_session_token(user_id=str(uuid.uuid4()), username="superadmin", is_admin=True, is_superadmin=True)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    r = _CSRFTestClient(client, csrf).patch(f"/admin/companies/{co.id}", json={"name": "Renamed", "status": "active"})
    assert r.status_code == 200, r.text
    assert _web_me(client, web) == 200
