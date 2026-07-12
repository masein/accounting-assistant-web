"""Owner user-management API — company-scoped, role-aware, self-lockout-safe."""
from __future__ import annotations

import uuid

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.core.permissions import Role
from app.models.company import Company
from app.models.entity import Entity
from app.models.user import User
from tests.conftest import _CSRFTestClient

PW = "Passw0rd!"


def _mk_company(db, slug: str) -> Company:
    c = Company(id=uuid.uuid4(), name=f"Co {slug}", slug=slug, locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.flush()
    return c


def _mk_user(db, company, username, role=Role.OWNER, active=True) -> User:
    from app.core.auth import hash_password
    h, s = hash_password(PW)
    u = User(id=uuid.uuid4(), username=username, password_hash=h, password_salt=s,
             preferred_language="en", role=role, is_admin=(role == Role.OWNER),
             is_active=active, company_id=company.id, token_version=0)
    db.add(u)
    db.flush()
    return u


def _as(client, user, company) -> _CSRFTestClient:
    token = create_session_token(
        user_id=str(user.id), username=user.username, is_admin=user.is_admin,
        company_id=str(company.id), token_version=user.token_version, role=user.role,
    )
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def owner_ctx(db, client):
    company = _mk_company(db, f"acme-{uuid.uuid4().hex[:8]}")
    owner = _mk_user(db, company, f"owner-{uuid.uuid4().hex[:6]}")
    db.flush()
    return _as(client, owner, company), company, owner


def test_owner_creates_users_with_roles(owner_ctx):
    api, company, owner = owner_ctx
    for role in (Role.CFO, Role.ACCOUNTANT, Role.MANAGER, Role.EMPLOYEE, Role.VIEWER):
        r = api.post("/admin/users", json={"username": f"{role}-x", "password": PW, "role": role})
        assert r.status_code == 201, r.text
        assert r.json()["role"] == role
        assert r.json()["is_admin"] is (role == Role.OWNER)


def test_list_is_company_scoped(owner_ctx, db, client):
    api, company, owner = owner_ctx
    # a user in ANOTHER company must not appear
    other = _mk_company(db, f"other-{uuid.uuid4().hex[:8]}")
    _mk_user(db, other, "intruder", role=Role.CFO)
    api.post("/admin/users", json={"username": "mycfo", "password": PW, "role": Role.CFO})
    db.flush()
    usernames = {u["username"] for u in api.get("/admin/users").json()}
    assert "mycfo" in usernames and "intruder" not in usernames


def test_invalid_role_rejected(owner_ctx):
    api, *_ = owner_ctx
    r = api.post("/admin/users", json={"username": "x", "password": PW, "role": "superadmin"})
    assert r.status_code == 400


def test_duplicate_username_rejected(owner_ctx):
    api, *_ = owner_ctx
    api.post("/admin/users", json={"username": "dup", "password": PW, "role": Role.CFO})
    r = api.post("/admin/users", json={"username": "dup", "password": PW, "role": Role.VIEWER})
    assert r.status_code == 400


def test_role_change_bumps_token_version(owner_ctx, db):
    api, company, owner = owner_ctx
    uid = api.post("/admin/users", json={"username": "grow", "password": PW, "role": Role.EMPLOYEE}).json()["id"]
    before = db.get(User, uuid.UUID(uid)).token_version
    r = api.patch(f"/admin/users/{uid}", json={"role": Role.ACCOUNTANT})
    assert r.status_code == 200 and r.json()["role"] == Role.ACCOUNTANT
    assert db.get(User, uuid.UUID(uid)).token_version == before + 1


def test_deactivate_bumps_token_version_and_blocks_self(owner_ctx, db):
    api, company, owner = owner_ctx
    uid = api.post("/admin/users", json={"username": "temp", "password": PW, "role": Role.VIEWER}).json()["id"]
    before = db.get(User, uuid.UUID(uid)).token_version
    assert api.patch(f"/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert db.get(User, uuid.UUID(uid)).token_version == before + 1
    # owner cannot deactivate themselves
    assert api.patch(f"/admin/users/{owner.id}", json={"is_active": False}).status_code == 400


def test_cannot_change_own_role_or_demote_last_owner(owner_ctx):
    api, company, owner = owner_ctx
    assert api.patch(f"/admin/users/{owner.id}", json={"role": Role.VIEWER}).status_code == 400


def test_cross_company_target_is_404(owner_ctx, db):
    api, company, owner = owner_ctx
    other = _mk_company(db, f"z-{uuid.uuid4().hex[:8]}")
    victim = _mk_user(db, other, "victim", role=Role.CFO)
    db.flush()
    assert api.patch(f"/admin/users/{victim.id}", json={"role": Role.VIEWER}).status_code == 404
    assert api.delete(f"/admin/users/{victim.id}").status_code == 404


def test_entity_link_requires_employee(owner_ctx, db, client):
    api, company, owner = owner_ctx
    emp = Entity(id=uuid.uuid4(), name="Jane Emp", type="employee", company_id=company.id)
    cust = Entity(id=uuid.uuid4(), name="Acme Ltd", type="customer", company_id=company.id)
    db.add_all([emp, cust]); db.flush()
    uid = api.post("/admin/users", json={"username": "jane", "password": PW, "role": Role.EMPLOYEE}).json()["id"]
    assert api.patch(f"/admin/users/{uid}", json={"entity_id": str(cust.id)}).status_code == 400
    r = api.patch(f"/admin/users/{uid}", json={"entity_id": str(emp.id)})
    assert r.status_code == 200 and r.json()["entity_id"] == str(emp.id)
    assert api.patch(f"/admin/users/{uid}", json={"unlink_entity": True}).json()["entity_id"] is None


def test_non_owner_cannot_manage_users(client):
    # a CFO session (not in DB) must be denied by the RBAC guard
    token = create_session_token(user_id=str(uuid.uuid4()), username="cfo", is_admin=False, role=Role.CFO)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    api = _CSRFTestClient(client, csrf)
    assert api.get("/admin/users").status_code == 403
    assert api.post("/admin/users", json={"username": "x", "password": PW, "role": Role.VIEWER}).status_code == 403
