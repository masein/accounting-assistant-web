"""Security review 2026-09-24 H6: admin and super-admin actions left no trace,
and the audit log was "immutable" only in a comment. Every mutation in
/admin/* and /admin/companies/* now writes an audit row naming the actor, and
audit rows can neither be edited nor deleted through the ORM (a Postgres
trigger enforces the same rule in production — migration 036)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.models.audit_log import AuditLog, AuditLogImmutableError
from app.models.company import Company
from tests.conftest import _CSRFTestClient


def _events(db, entity_type, entity_id=None):
    q = select(AuditLog).where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == str(entity_id))
    return db.execute(q).scalars().all()


def _company_owner(client, db):
    c = Company(id=uuid.uuid4(), name="Audit Co", slug=f"audit-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c); db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="audit-owner", is_admin=True, role="owner",
                               company_id=str(c.id))
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return c, _CSRFTestClient(client, csrf)


def test_period_lock_and_settings_are_audited(auth_client, db):
    assert auth_client.put("/admin/closed-period", json={"closed_period": "2024-03-31"}).status_code == 200
    assert auth_client.put("/admin/closed-period", json={"closed_period": None}).status_code == 200
    acts = [e.action for e in _events(db, "closed_period")]
    assert "lock_period" in acts and "reopen_period" in acts
    lock = next(e for e in _events(db, "closed_period") if e.action == "lock_period")
    assert lock.username == "testuser" and "2024-03-31" in (lock.detail or "")

    assert auth_client.put("/admin/display-calendar", json={"calendar": "jalali"}).status_code == 200
    assert any(e.detail == "jalali" for e in _events(db, "setting", "display_calendar"))


def test_user_lifecycle_is_audited(client, db):
    _co, owner = _company_owner(client, db)
    r = owner.post("/admin/users", json={"username": f"aud-{uuid.uuid4().hex[:6]}", "password": "Strong#Pass2026", "role": "viewer"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert [e.action for e in _events(db, "user", uid)] == ["create"]
    assert json.loads(_events(db, "user", uid)[0].detail)["role"] == "viewer"

    assert owner.patch(f"/admin/users/{uid}", json={"role": "accountant", "password": "Another#Pass2026"}).status_code == 200
    upd = next(e for e in _events(db, "user", uid) if e.action == "update")
    detail = json.loads(upd.detail)
    assert detail["role"] == "accountant" and detail["password_reset"] is True
    assert "Another#Pass2026" not in upd.detail  # never the secret

    assert owner.delete(f"/admin/users/{uid}").status_code == 204
    assert sorted(e.action for e in _events(db, "user", uid)) == ["create", "delete", "update"]


def test_api_keys_are_audited_without_the_secret(client, db):
    _co, owner = _company_owner(client, db)
    r = owner.post("/admin/api-keys", json={"label": "ci"})
    assert r.status_code == 201, r.text
    key_id, raw = r.json()["id"], r.json().get("key") or r.json().get("api_key") or ""
    created = _events(db, "api_key", key_id)
    assert [e.action for e in created] == ["create"]
    if raw:
        assert raw not in (created[0].detail or "")
    assert owner.delete(f"/admin/api-keys/{key_id}").status_code == 204
    assert sorted(e.action for e in _events(db, "api_key", key_id)) == ["create", "revoke"]


def test_platform_ai_config_change_is_audited_without_the_key(superadmin_client, db, monkeypatch):
    from app.db import session as db_session
    from tests.conftest import _TestSession
    monkeypatch.setattr(db_session, "SessionLocal", _TestSession)
    before = len(_events(db, "ai_config", "platform"))
    r = superadmin_client.patch("/admin/ai-config", json={"model": "gpt-4.1-mini", "api_key": "tpsg-SECRET-123"})
    assert r.status_code == 200, r.text
    events = _events(db, "ai_config", "platform")
    assert len(events) == before + 1
    detail = json.loads(events[-1].detail)
    assert detail["model"] == "gpt-4.1-mini" and detail["api_key_changed"] is True
    assert "SECRET" not in events[-1].detail


def _purge_company(db, cid):
    """provision_company seeds a whole chart for the new tenant; queries that
    run without a tenant context (most of the suite) would then see duplicate
    account codes. Remove every row of the company, then the company."""
    from sqlalchemy import delete
    from app.db.base import Base
    from app.db.tenant import tenant_bypass
    db.rollback()
    with tenant_bypass():
        for table in reversed(Base.metadata.sorted_tables):
            if "company_id" in table.c and table.name != "companies":
                db.execute(delete(table).where(table.c.company_id == uuid.UUID(cid)))
        from app.models.user import User
        db.execute(delete(User).where(User.company_id == uuid.UUID(cid)))
        db.execute(delete(Company).where(Company.id == uuid.UUID(cid)))
        db.commit()


def test_tenant_console_actions_are_audited(superadmin_client, db):
    r = superadmin_client.post("/admin/companies", json={"name": f"Trail {uuid.uuid4().hex[:4]}", "locale": "ir",
                                                          "username": f"trail-{uuid.uuid4().hex[:6]}", "password": "Strong#Pass2026"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    try:
        _tenant_console_asserts(superadmin_client, db, cid)
    finally:
        _purge_company(db, cid)


def _tenant_console_asserts(superadmin_client, db, cid):
    assert [e.action for e in _events(db, "company", cid)] == ["create"]
    assert superadmin_client.patch(f"/admin/companies/{cid}", json={"status": "suspended"}).status_code == 200
    upd = next(e for e in _events(db, "company", cid) if e.action == "update")
    assert json.loads(upd.detail) == {"status": "suspended"}
    rp = superadmin_client.post(f"/admin/companies/{cid}/reset-password", json={"password": "Another#Pass2026"})
    assert rp.status_code == 200, rp.text
    resets = [e for e in db.execute(select(AuditLog).where(AuditLog.action == "reset_password")).scalars().all()
              if cid in (e.detail or "")]
    assert resets and "Another#Pass2026" not in resets[0].detail


def test_audit_rows_cannot_be_edited_or_deleted_through_the_orm(db):
    from app.services.audit_service import log_audit_event
    row = log_audit_event(db, action="probe", entity_type="probe", entity_id="x")
    db.commit()
    row.detail = "rewritten history"
    with pytest.raises(AuditLogImmutableError):
        db.flush()
    db.rollback()
    row = db.get(AuditLog, row.id)
    db.delete(row)
    with pytest.raises(AuditLogImmutableError):
        db.flush()
    db.rollback()
    assert db.get(AuditLog, row.id).detail is None


def test_migration_036_installs_the_trigger_and_keeps_the_trail_on_company_delete():
    src = (Path(__file__).resolve().parents[1] / "alembic/versions/036_audit_logs_append_only.py").read_text()
    assert "BEFORE UPDATE OR DELETE ON audit_logs" in src
    assert "RAISE EXCEPTION" in src
    assert "ON DELETE RESTRICT" in src
    assert 'down_revision: Union[str, None] = "035"' in src
