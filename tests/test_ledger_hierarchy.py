"""Feedback 3/3 — کل/معین/تفضیلی surfacing + bank سرفصل on manual create.

Shared test chart is the Iranian seed (کل = 2-digit GROUP, معین = 4-digit
GENERAL); تفضیلی is the per-entity sub-ledger.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.models.account import Account
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from tests.conftest import _CSRFTestClient


def _company(db):
    c = Company(id=uuid.uuid4(), name="Co", slug=f"co-{uuid.uuid4().hex[:8]}",
                locale="ir", base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.flush()
    return c


def _api(client, company=None):
    tok = create_session_token(
        user_id=str(uuid.uuid4()), username="owner", is_admin=True,
        company_id=(str(company.id) if company else None), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


# --- bank سرفصل on manual create --------------------------------------------

def test_manual_bank_create_links_gl_account(db, client):
    api = _api(client)
    name = f"بانک ملی {uuid.uuid4().hex[:6]}"
    r = api.post("/entities", json={"type": "bank", "name": name})
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert code, "bank entity must carry its own GL account code (سرفصل)"
    acc = db.query(Account).filter(Account.code == code).first()
    assert acc is not None, "the linked GL account must actually exist"
    assert code.startswith("111")  # Iranian cash/bank range


def test_manual_bank_create_respects_existing_account_code(db, client):
    api = _api(client)
    r = api.post("/entities", json={"type": "bank", "name": f"B {uuid.uuid4().hex[:6]}",
                                    "code": "1110"})
    assert r.status_code == 201
    assert r.json()["code"] == "1110"  # reused the referenced cash account


def test_non_bank_entities_get_no_auto_account(db, client):
    api = _api(client)
    r = api.post("/entities", json={"type": "client", "name": f"C {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201
    assert r.json()["code"] is None


# --- کل (parent) in ledger reports ------------------------------------------

def test_ledger_summary_carries_kol_parent(db, client, make_transaction):
    make_transaction([("1110", 1000, 0), ("4110", 0, 1000)])
    db.commit()
    api = _api(client)
    rows = api.get("/reports/ledger-summary").json()["rows"]
    row = next(r for r in rows if r["account_code"] == "1110")
    assert row["parent_code"] == "11"       # کل: دارایی‌های جاری
    assert row["parent_name"]
    row4 = next(r for r in rows if r["account_code"] == "4110")
    assert row4["parent_code"] == "41"


def test_account_detail_carries_kol_parent(db, client, make_transaction):
    make_transaction([("1110", 500, 0), ("4110", 0, 500)])
    db.commit()
    api = _api(client)
    d = api.get("/reports/accounts/1110/detail").json()
    assert d["parent_code"] == "11" and d["parent_name"]


# --- تفضیلی: locale-aware person ledger --------------------------------------

def test_person_ledger_uses_locale_ar_code(db, make_transaction):
    """IR behaviour preserved via the resolver (ar → 1112), no hardcoding."""
    from app.services.reporting.operations_report_service import OperationsReportService
    ent = Entity(name=f"Client {uuid.uuid4().hex[:6]}", type="client")
    db.add(ent)
    db.flush()
    txn = make_transaction([("1112", 2000, 0), ("4110", 0, 2000)],
                           tx_date=date(2026, 3, 10))
    db.add(TransactionEntity(transaction_id=txn.id, entity_id=ent.id, role="client"))
    db.flush()
    res = OperationsReportService(db).person_running_balance(
        ent.id, "client", date(2026, 3, 1), date(2026, 3, 31))
    assert len(res.rows) == 1
    assert res.rows[0].running_balance == 2000


def test_person_ledger_bank_matches_entity_own_account(db, make_transaction):
    """A bank's sub-ledger follows ITS OWN linked GL account, not just 1110."""
    from app.services.account_resolver import _ensure_account
    from app.services.reporting.operations_report_service import OperationsReportService
    _ensure_account(db, "1113", "بانک تست", "ir")
    bank = Entity(name=f"Bank {uuid.uuid4().hex[:6]}", type="bank", code="1113")
    db.add(bank)
    db.flush()
    txn = make_transaction([("1113", 700, 0), ("4110", 0, 700)], tx_date=date(2026, 3, 12))
    db.add(TransactionEntity(transaction_id=txn.id, entity_id=bank.id, role="bank"))
    db.flush()
    res = OperationsReportService(db).person_running_balance(
        bank.id, "bank", date(2026, 3, 1), date(2026, 3, 31))
    assert len(res.rows) == 1 and res.rows[0].running_balance == 700
