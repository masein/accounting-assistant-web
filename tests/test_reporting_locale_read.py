"""Display facts every page needs are readable by every role; only the
settings roles change them. Without the reporting locale an accountant,
manager, employee or viewer in an Iranian company got the generic balance
sheet, income statement and cash flow instead of the Iranian templates (and a
UK user the Iranian default bank account)."""
from __future__ import annotations

import uuid

import pytest

from app.core.permissions import ANY_ROLE, ROUTE_PERMISSIONS, Perm

ROLES = ["accountant", "manager", "employee", "viewer"]


def _login(client, role, company_id=None):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=role == "owner", role=role,
                               company_id=company_id)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def test_the_rule_reads_for_everyone_and_writes_for_settings():
    assert ROUTE_PERMISSIONS[("GET", "/admin/reporting-locale")] == ANY_ROLE
    assert ROUTE_PERMISSIONS[("PUT", "/admin/reporting-locale")] == Perm.SETTINGS_WRITE
    # the rest of that group stays behind settings access
    for path in ("/admin/iran-shares-outstanding", "/admin/closed-period"):
        assert ROUTE_PERMISSIONS[("GET", path)] == Perm.SETTINGS_READ


@pytest.mark.parametrize("role", ROLES)
def test_every_role_reads_the_reporting_locale_but_cannot_change_it(client, role):
    api = _login(client, role)
    try:
        r = api.get("/admin/reporting-locale")
        assert r.status_code == 200, r.text
        assert r.json()["locale"] in r.json()["supported"]
        assert api.put("/admin/reporting-locale", json={"locale": "uk"}).status_code == 403
    finally:
        client.cookies.clear()


@pytest.fixture()
def iranian_company(db):
    from app.models.company import Company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Tehran Books", slug=f"loc-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    db.expunge_all()
    yield cid
    _purge_company(db, cid)


@pytest.mark.parametrize("role", ROLES)
def test_an_accountant_in_an_iranian_company_sees_ir(client, iranian_company, role):
    """What the Manager-reports page keys the Iranian statements on."""
    api = _login(client, role, company_id=iranian_company)
    try:
        assert api.get("/admin/reporting-locale").json()["locale"] == "ir"
    finally:
        client.cookies.clear()


def test_the_owner_still_changes_it(client, iranian_company):
    api = _login(client, "owner", company_id=iranian_company)
    try:
        assert api.put("/admin/reporting-locale", json={"locale": "ir"}).status_code == 200
    finally:
        client.cookies.clear()
