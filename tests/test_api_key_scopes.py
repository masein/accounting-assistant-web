"""Integration API keys carry scopes and an expiry (roadmap 2026-09 §1.13).

A key is limited to what it was created for (``time:read`` / ``time:write``),
expires after a year unless the owner picks otherwise, is refused once
expired, and the owner is warned two weeks before it stops working.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.api_key_auth import (
    DEFAULT_EXPIRY_DAYS,
    SCOPES,
    generate_api_key,
    is_expired,
    normalize_scopes,
    parse_scopes,
)
from app.db.tenant import use_company
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.entity import Entity
from app.models.notification import Notification
from app.services.notification_service import refresh_notifications


# --- helpers -------------------------------------------------------------------

def _company(db):
    c = Company(id=uuid.uuid4(), name="Keys Ltd", slug=f"keys-{uuid.uuid4().hex[:8]}",
                locale="uk", base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.flush()
    return c


def _key(db, company, *, scopes="time:read,time:write", expires_at=None, label="jira"):
    raw, digest, prefix = generate_api_key()
    k = ApiKey(company_id=company.id, label=label, key_hash=digest, prefix=prefix,
               scopes=scopes, expires_at=expires_at)
    db.add(k)
    db.flush()
    return raw, k


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def _entry(src, **over):
    body = {"external_id": f"W-{uuid.uuid4().hex[:6]}", "source": src,
            "worker": "hana@keys.test", "date": "2026-01-05", "hours": 2,
            "description": "Scoped push"}
    body.update(over)
    return body


def _owner_api(client, company):
    from tests.conftest import _CSRFTestClient
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings as app_settings
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True,
                               company_id=str(company.id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(app_settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def world(db, client):
    co = _company(db)
    emp = Entity(id=uuid.uuid4(), name="Hana", type="employee", email="hana@keys.test",
                 company_id=co.id)
    db.add(emp)
    db.flush()
    return {"co": co, "emp": emp, "client": client, "src": f"jira-{uuid.uuid4().hex[:8]}"}


# --- pure helpers ----------------------------------------------------------------

def test_normalize_scopes_dedupes_sorts_and_refuses_unknown():
    assert normalize_scopes(["time:write", "time:read", "time:write"]) == ["time:read", "time:write"]
    assert normalize_scopes([" time:read "]) == ["time:read"]
    with pytest.raises(ValueError):
        normalize_scopes(["time:read", "ledger:write"])
    with pytest.raises(ValueError):
        normalize_scopes([])


def test_parse_scopes_tolerates_blanks_and_legacy_nulls():
    assert parse_scopes("time:read, time:write") == {"time:read", "time:write"}
    assert parse_scopes("time:read") == {"time:read"}
    # a key stored before scopes existed had every capability it has today
    assert parse_scopes(None) == {"time:read", "time:write"}
    # an explicitly empty scope list is allowed nothing
    assert parse_scopes("") == set()
    assert parse_scopes(" , ") == set()


def test_is_expired_handles_naive_and_aware_datetimes():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    assert not is_expired(None, now)
    assert is_expired(now - timedelta(seconds=1), now)
    assert not is_expired(now + timedelta(days=1), now)
    # SQLite hands back naive datetimes — they are UTC
    assert is_expired(datetime(2026, 9, 25, 11, 59), now)
    assert not is_expired(datetime(2026, 9, 25, 12, 1), now)


# --- enforcement on /api/v1 ------------------------------------------------------------

def test_read_only_key_can_list_but_not_push_or_delete(world, db):
    raw, _ = _key(db, world["co"], scopes="time:read")
    c = world["client"]
    assert c.get("/api/v1/time-entries", headers=_bearer(raw)).status_code == 200
    r = c.post("/api/v1/time-entries", json=_entry(world["src"]), headers=_bearer(raw))
    assert r.status_code == 403
    assert "time:write" in r.json()["detail"]
    r = c.delete(f"/api/v1/time-entries/by-external/{world['src']}/W-1", headers=_bearer(raw))
    assert r.status_code == 403


def test_write_only_key_can_push_but_not_list(world, db):
    raw, _ = _key(db, world["co"], scopes="time:write")
    c = world["client"]
    r = c.post("/api/v1/time-entries", json=_entry(world["src"]), headers=_bearer(raw))
    assert r.status_code == 200, r.text
    r = c.get("/api/v1/time-entries", headers=_bearer(raw))
    assert r.status_code == 403
    assert "time:read" in r.json()["detail"]


def test_key_with_no_scopes_is_allowed_nothing(world, db):
    raw, _ = _key(db, world["co"], scopes="")
    c = world["client"]
    assert c.get("/api/v1/time-entries", headers=_bearer(raw)).status_code == 403
    assert c.post("/api/v1/time-entries", json=_entry(world["src"]), headers=_bearer(raw)).status_code == 403


def test_full_key_does_both(world, db):
    raw, _ = _key(db, world["co"])
    c = world["client"]
    assert c.post("/api/v1/time-entries", json=_entry(world["src"]), headers=_bearer(raw)).status_code == 200
    assert c.get("/api/v1/time-entries", headers=_bearer(raw)).status_code == 200


def test_expired_key_is_401_and_last_used_not_touched(world, db):
    raw, key = _key(db, world["co"], expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    c = world["client"]
    assert c.get("/api/v1/time-entries", headers=_bearer(raw)).status_code == 401
    assert c.post("/api/v1/time-entries", json=_entry(world["src"]), headers=_bearer(raw)).status_code == 401
    db.refresh(key)
    assert key.last_used_at is None


def test_key_expiring_later_still_works(world, db):
    raw, _ = _key(db, world["co"], expires_at=datetime.now(timezone.utc) + timedelta(days=3))
    assert world["client"].get("/api/v1/time-entries", headers=_bearer(raw)).status_code == 200


def test_scoped_key_still_cannot_reach_the_rest_of_the_app(world, db):
    raw, _ = _key(db, world["co"])
    c = world["client"]
    for path in ("/transactions", "/admin/api-keys", "/reports/dashboard"):
        assert c.get(path, headers=_bearer(raw)).status_code in (401, 403), path


# --- owner management ------------------------------------------------------------------

def test_scope_catalogue_is_published(world):
    api = _owner_api(world["client"], world["co"])
    r = api.get("/admin/api-keys/scopes")
    assert r.status_code == 200
    body = r.json()
    assert set(body["scopes"]) == set(SCOPES)
    assert body["default_expiry_days"] == DEFAULT_EXPIRY_DAYS


def test_create_defaults_to_both_scopes_and_one_year(world, db):
    api = _owner_api(world["client"], world["co"])
    r = api.post("/admin/api-keys", json={"label": "toggl"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scopes"] == ["time:read", "time:write"]
    exp = datetime.fromisoformat(body["expires_at"])
    exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    days = (exp - datetime.now(timezone.utc)).days
    assert DEFAULT_EXPIRY_DAYS - 1 <= days <= DEFAULT_EXPIRY_DAYS
    assert body["expired"] is False
    assert body["api_key"].startswith("ak_")


def test_create_with_chosen_scopes_and_expiry(world, db):
    api = _owner_api(world["client"], world["co"])
    r = api.post("/admin/api-keys", json={"label": "ro", "scopes": ["time:read"], "expires_in_days": 30})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scopes"] == ["time:read"]
    row = db.get(ApiKey, uuid.UUID(body["id"]))
    assert row.scopes == "time:read"
    # the created key really is read-only
    c = world["client"]
    raw = body["api_key"]
    assert c.get("/api/v1/time-entries", headers=_bearer(raw)).status_code == 200
    assert c.post("/api/v1/time-entries", json=_entry(world["src"]), headers=_bearer(raw)).status_code == 403


def test_create_never_expiring_key(world, db):
    api = _owner_api(world["client"], world["co"])
    r = api.post("/admin/api-keys", json={"label": "forever", "expires_in_days": None})
    assert r.status_code == 201, r.text
    assert r.json()["expires_at"] is None
    assert r.json()["expired"] is False


@pytest.mark.parametrize("body", [
    {"label": "x", "scopes": ["ledger:write"]},
    {"label": "x", "scopes": []},
    {"label": "x", "expires_in_days": 0},
    {"label": "x", "expires_in_days": 731},
])
def test_create_rejects_bad_scopes_and_expiry(world, body):
    api = _owner_api(world["client"], world["co"])
    assert api.post("/admin/api-keys", json=body).status_code == 422


def test_create_is_audited_with_scopes_and_expiry(world, db):
    api = _owner_api(world["client"], world["co"])
    r = api.post("/admin/api-keys", json={"label": "audited", "scopes": ["time:write"], "expires_in_days": 90})
    assert r.status_code == 201
    kid = r.json()["id"]
    row = db.execute(select(AuditLog).where(AuditLog.entity_id == kid)).scalars().first()
    assert row is not None
    detail = json.loads(row.detail)
    assert detail["scopes"] == ["time:write"]
    assert detail["expires_at"]
    assert r.json()["api_key"] not in row.detail  # the secret never lands in the audit log


def test_list_shows_scopes_expiry_and_expired_flag(world, db):
    _key(db, world["co"], label="old", expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    _key(db, world["co"], label="ro", scopes="time:read")
    api = _owner_api(world["client"], world["co"])
    rows = {k["label"]: k for k in api.get("/admin/api-keys").json()}
    assert rows["old"]["expired"] is True
    assert rows["ro"]["scopes"] == ["time:read"]
    assert rows["ro"]["expires_at"] is None and rows["ro"]["expired"] is False
    assert all("key_hash" not in k and "api_key" not in k for k in rows.values())


# --- owner warning before expiry -------------------------------------------------------------

def _notes(db, company):
    with use_company(company.id):
        return {n.dedupe_key: n for n in db.execute(
            select(Notification).where(Notification.kind == "api_key")).scalars().all()}


def test_owner_is_warned_two_weeks_before_expiry(world, db):
    today = date(2026, 9, 25)
    at = lambda d: datetime.combine(today + timedelta(days=d), datetime.min.time(), tzinfo=timezone.utc)
    _, soon = _key(db, world["co"], label="soon", expires_at=at(10))
    _, later = _key(db, world["co"], label="later", expires_at=at(60))
    _, gone = _key(db, world["co"], label="gone", expires_at=at(-2))
    _, revoked = _key(db, world["co"], label="revoked", expires_at=at(5))
    revoked.revoked = True
    _, never = _key(db, world["co"], label="never", expires_at=None)
    db.flush()
    with use_company(world["co"].id):
        refresh_notifications(db, today=today)
        db.flush()
    notes = _notes(db, world["co"])
    assert set(notes) == {f"apikey-{soon.id}", f"apikey-{gone.id}"}
    assert notes[f"apikey-{soon.id}"].level == "warning"
    assert "10 day" in notes[f"apikey-{soon.id}"].title
    assert notes[f"apikey-{gone.id}"].level == "high"
    assert "expired" in notes[f"apikey-{gone.id}"].title
    assert notes[f"apikey-{soon.id}"].link_page == "settings"


def test_revoking_clears_the_warning(world, db):
    today = date(2026, 9, 25)
    _, k = _key(db, world["co"], label="soon",
                expires_at=datetime.combine(today + timedelta(days=3), datetime.min.time(), tzinfo=timezone.utc))
    with use_company(world["co"].id):
        refresh_notifications(db, today=today)
        db.flush()
    assert f"apikey-{k.id}" in _notes(db, world["co"])
    k.revoked = True
    db.flush()
    with use_company(world["co"].id):
        refresh_notifications(db, today=today)
        db.flush()
    assert _notes(db, world["co"])[f"apikey-{k.id}"].dismissed_at is not None


def test_api_key_warnings_are_owner_only():
    from app.services.notification_service import KIND_ROLES
    assert KIND_ROLES["api_key"] == ("owner",)
