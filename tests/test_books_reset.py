"""A company's books cleared, to import them again (scenarios AI1–AI7).

The owner chose to clear two schools imported before the journal importer
kept their own codes, and import their files again (2026-10-19). A made-up
school, imported as theirs were, in a company of its own (tests share one
database)."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.tenant import tenant_bypass, use_company
from app.models.account import Account
from app.models.ai_accountant import AIChatSession
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.models.entity import Entity
from app.models.transaction import Transaction
from app.models.user import User
from tests.test_hand_kept_books import _journal_rows, _preview, _workbook
from tests.test_posting_roles import co  # noqa: F401 — the fixture


def _superadmin(client):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="superadmin", is_admin=True, is_superadmin=True))
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _owner(client, cid):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="owner", is_admin=True, company_id=cid, role="owner"))
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _import_school(api) -> int:
    p = _preview(api, _workbook(_journal_rows()))
    out = api.post("/migration/journals/apply", json={"token": p["token"], "years": "fix"})
    assert out.status_code == 200, out.text
    return out.json()["posted"]


def _count(db, cid, model, *where):
    with use_company(cid):
        return db.execute(select(func.count()).select_from(model).where(*where)).scalar()


def test_ai1_to_ai6_a_school_cleared_by_the_platform_admin_and_imported_again(co, client, db):
    cid = str(co["company"].id)
    api = co["api"]
    posted = _import_school(api)
    assert posted > 0
    r = api.post("/ai-accountant/chat", json={"message": "سلام"})            # a chat of its own
    with use_company(cid):
        db.add_all([AppSetting(key="closed_period", value="2025-01-01"), AppSetting(key="display_calendar", value="jalali")])
        db.add(User(username=f"father-{uuid.uuid4().hex[:6]}", password_hash="x", password_salt="y", company_id=uuid.UUID(cid),
                    role="accountant", is_active=True))
        db.commit()
    # another company, untouched by all of it
    from app.services.company_service import provision_company
    other, _u = provision_company(db, name=f"other {uuid.uuid4().hex[:4]}", locale="ir", base_currency="IRR",
                                  username=f"other-{uuid.uuid4().hex[:6]}", password="Str0ng#Pass2026")
    other_id = str(other.id)
    with use_company(other_id):
        other_accounts = db.execute(select(func.count()).select_from(Account)).scalar()
    from app.models.transaction_fee import PaymentMethod
    with tenant_bypass():
        methods = db.execute(select(func.count()).select_from(PaymentMethod)).scalar()

    admin = _superadmin(client)
    # AI1: what would go — nothing changes
    books = admin.get(f"/admin/companies/{cid}/books").json()
    assert books["name"] == co["company"].name
    assert books["tables"]["transactions"] == posted and books["tables"]["accounts"] > 0
    assert books["tables"]["entities"] >= 1 and books["tables"].get("ai_chat_sessions", 0) >= (1 if r.status_code == 200 else 0)
    assert "journal_import_account_map" in books["settings"] and "closed_period" in books["settings"]
    assert "users" not in books["tables"] and "audit_logs" not in books["tables"]
    assert _count(db, cid, Transaction) == posted

    # AI2: a name that isn't the school's: refused, nothing changes
    bad = admin.post(f"/admin/companies/{cid}/reset-books", json={"confirm": "منادی"})
    assert bad.status_code == 422 and "exactly" in bad.json()["detail"]
    assert _count(db, cid, Transaction) == posted

    # AI3: cleared, and seeded again as a new company's
    done = admin.post(f"/admin/companies/{cid}/reset-books", json={"confirm": co["company"].name})
    assert done.status_code == 200, done.text
    assert done.json()["removed"]["transactions"] == posted and done.json()["accounts_seeded"] > 0
    db.expire_all()
    assert _count(db, cid, Transaction) == 0 and _count(db, cid, Entity) == 0 and _count(db, cid, AIChatSession) == 0
    with use_company(cid):
        codes = set(db.execute(select(Account.code)).scalars())
        keys = set(db.execute(select(AppSetting.key)).scalars())
        assert "1110" in codes and "4110" in codes                                 # the seeded chart
        assert not any(c.startswith("1151") or c.startswith("6131") for c in codes)  # none of the import's
        # AI3, AI4: the settings naming the old accounts went; the configuration stayed
        assert not keys & {"journal_import_account_map", "posting_roles", "closed_period"}
        assert "display_calendar" in keys
    with tenant_bypass():
        # AI4: its logins stay, and its audit trail, with the reset in it
        assert db.execute(select(func.count()).select_from(User).where(User.company_id == uuid.UUID(cid))).scalar() >= 1
        row = db.execute(select(AuditLog).where(AuditLog.action == "reset_books", AuditLog.entity_id == cid)).scalars().one()
        assert '"transactions"' in row.detail
        # AI5: nobody else's — another company's chart, and the platform's payment methods
        assert db.execute(select(func.count()).select_from(PaymentMethod)).scalar() == methods
    with use_company(other_id):
        assert db.execute(select(func.count()).select_from(Account)).scalar() == other_accounts

    # AI6: the school's files go in again, whole
    api = _owner(client, cid)
    assert _import_school(api) == posted
    from tests.test_admin_audit import _purge_company
    _purge_company(db, other_id)


def test_ai2_only_the_platform_admin_resets_a_company(co):
    cid = str(co["company"].id)
    r = co["api"].post(f"/admin/companies/{cid}/reset-books", json={"confirm": co["company"].name})
    assert r.status_code == 403
    assert co["api"].get(f"/admin/companies/{cid}/books").status_code == 403


def test_ai7_the_owners_own_wipe_clears_every_table_of_its_books(co, client, db):
    """Settings' «پاک کردن دفاتر»: it missed the chats, imports and the settings naming the old
    accounts, and emptied every company's payment methods."""
    cid = str(co["company"].id)
    api = co["api"]
    _import_school(api)
    api.post("/ai-accountant/chat", json={"message": "سلام"})
    from app.models.transaction_fee import PaymentMethod
    with tenant_bypass():
        methods = db.execute(select(func.count()).select_from(PaymentMethod)).scalar()
    r = api.post("/admin/reset-db")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert _count(db, cid, Transaction) == 0 and _count(db, cid, AIChatSession) == 0 and _count(db, cid, Entity) == 0
    with use_company(cid):
        keys = set(db.execute(select(AppSetting.key)).scalars())
        assert not keys & {"journal_import_account_map", "posting_roles"}
        assert db.execute(select(func.count()).select_from(Account)).scalar() > 0
    with tenant_bypass():
        assert db.execute(select(func.count()).select_from(PaymentMethod)).scalar() >= methods


def test_ai5_a_platform_admin_with_no_company_open_cant_empty_every_company(client, db):
    """Settings' wipe with no company open emptied every company's books: a company's are reset
    from the companies console."""
    admin = _superadmin(client)
    with tenant_bypass():
        before = db.execute(select(func.count()).select_from(Account)).scalar()
    r = admin.post("/admin/reset-db")
    assert r.status_code == 400 and "Companies" in r.json()["detail"]
    with tenant_bypass():
        assert db.execute(select(func.count()).select_from(Account)).scalar() == before
