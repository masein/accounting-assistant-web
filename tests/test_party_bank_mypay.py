"""Party bank details, AI detail-gathering, payroll paid-to snapshot, and the
employee self-service my-payslips endpoint."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.tenant import tenant_bypass, use_company
from app.models.company import Company
from app.models.entity import Entity
from app.models.pay_run import PayRun, PayRunLine
from tests.conftest import _CSRFTestClient

BANK = {
    "bank_name": "Bank Melli",
    "account_holder": "Dana Rahimi",
    "account_number": "0123456789",
    "iban": "IR120170000000123456789012",
}


@pytest.fixture()
def company(db):
    c = Company(id=uuid.uuid4(), name="PayCo", slug=f"pay-{uuid.uuid4().hex[:8]}",
                locale="ir", base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.flush()
    with use_company(c.id):
        yield c
    from app.models.account import Account
    from app.models.ai_accountant import AIProposal
    from app.models.audit_log import AuditLog
    from app.models.transaction import Transaction, TransactionLine
    from app.models.entity import TransactionEntity
    db.rollback()
    with tenant_bypass():
        for Model in (TransactionLine, TransactionEntity, PayRunLine, PayRun,
                      AIProposal, AuditLog, Transaction, Entity, Account):
            db.query(Model).filter(Model.company_id == c.id).delete(synchronize_session=False)
        db.commit()


def _api(client, company, *, role="owner", entity_id=None, admin=True):
    tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=admin,
                               company_id=str(company.id), role=role, entity_id=entity_id)
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


# --- 1. bank fields round-trip ------------------------------------------------

def test_entity_bank_fields_roundtrip(db, client, company):
    api = _api(client, company)
    r = api.post("/entities", json={"type": "employee", "name": f"Dana {uuid.uuid4().hex[:6]}", **BANK,
                                    "phone": "0912 000 0000", "address": "Tehran"})
    assert r.status_code == 201, r.text
    d = r.json()
    for k, v in BANK.items():
        assert d[k] == v
    r2 = api.patch(f"/entities/{d['id']}", json={"iban": "IR999999999999999999999999"})
    assert r2.json()["iban"] == "IR999999999999999999999999"
    assert r2.json()["bank_name"] == "Bank Melli"


# --- 2. AI gathers details → one card → entity carries them -------------------

def test_ai_create_entity_with_details(db, company):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.proposal_tools import (
        ProposeCreateEntity, ProposeCreateEntityInput,
    )
    from app.services.ai_accountant.execute_service import execute_proposal

    ctx = ToolContext(db=db, user_id="u1", username="t",
                      user_message="add Dana as an employee, bank melli IR120170000000123456789012")
    out = asyncio.run(ProposeCreateEntity().run(ctx, ProposeCreateEntityInput(
        name=f"Dana {uuid.uuid4().hex[:6]}", type="employee",
        phone="0912 111 2233", address="Tehran, Valiasr", **BANK)))
    assert out["status"] == "pending"
    assert "phone" in out["summary"] and "Bank Melli" in str(out["preview"])
    res = execute_proposal(db, confirmation_token=out["confirmation_token"],
                           actor_user_id="u1", actor_username="t")
    db.commit()
    from app.models.audit_log import AuditLog
    audit = db.get(AuditLog, uuid.UUID(res.audit_log_id))
    ent = db.get(Entity, uuid.UUID(audit.entity_id))
    assert ent.phone == "0912 111 2233"
    assert ent.iban == BANK["iban"]
    assert ent.bank_name == "Bank Melli"


def test_ai_create_entity_notes_missing_details(db, company):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.proposal_tools import (
        ProposeCreateEntity, ProposeCreateEntityInput,
    )
    ctx = ToolContext(db=db, user_id="u1", username="t", user_message="just add them")
    out = asyncio.run(ProposeCreateEntity().run(ctx, ProposeCreateEntityInput(
        name=f"Acme {uuid.uuid4().hex[:6]}", type="client")))
    assert out["status"] == "pending"          # never blocks
    assert "missing" in out["summary"]         # but notes the gap
    out2 = asyncio.run(ProposeCreateEntity().run(ctx, ProposeCreateEntityInput(
        name=f"Emp {uuid.uuid4().hex[:6]}", type="employee")))
    assert "bank account" in out2["summary"]


# --- 3. pay → paid_to snapshot + warning --------------------------------------

def _mk_run(db, *, entity_id, name, net=1_000_000):
    run = PayRun(period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
                 pay_date=date(2026, 7, 1), currency="IRR", status="posted",
                 total_gross=net, total_net=net)
    db.add(run)
    db.flush()
    db.add(PayRunLine(run_id=run.id, entity_id=entity_id, employee_name=name,
                      gross=net, net_pay=net))
    db.flush()
    return run


def test_pay_snapshots_bank_and_warns_when_missing(db, client, company):
    api = _api(client, company)
    with_bank = Entity(name="Dana R", type="employee", **BANK)
    no_bank = Entity(name="Bob NoBank", type="employee")
    db.add_all([with_bank, no_bank])
    db.flush()

    run1 = _mk_run(db, entity_id=with_bank.id, name="Dana R")
    db.commit()
    r1 = api.post(f"/payroll/runs/{run1.id}/pay")
    assert r1.status_code == 200, r1.text
    assert r1.json()["warnings"] == []
    ln = db.execute(select(PayRunLine).where(PayRunLine.run_id == run1.id)).scalars().one()
    assert "Bank Melli" in ln.paid_to and BANK["iban"] in ln.paid_to

    run2 = _mk_run(db, entity_id=no_bank.id, name="Bob NoBank")
    db.commit()
    r2 = api.post(f"/payroll/runs/{run2.id}/pay")
    assert r2.status_code == 200
    warns = r2.json()["warnings"]
    assert len(warns) == 1 and "Bob NoBank" in warns[0]


def test_payslip_paid_to_line(db, company):
    from app.services.documents.labels import labels_for
    from app.services.documents.render import _paid_to_line
    emp = Entity(name="Dana R", type="employee", **BANK)
    db.add(emp)
    db.flush()
    line = type("L", (), {"paid_to": None})()
    s = _paid_to_line(line, emp, labels_for("uk"))
    assert s == f"Paid to: Bank Melli · {BANK['iban']}"
    line2 = type("L", (), {"paid_to": "Snapshotted · X"})()
    assert _paid_to_line(line2, emp, labels_for("ir")) == "واریز به: Snapshotted · X"
    assert _paid_to_line(type("L", (), {"paid_to": None})(), Entity(name="B", type="employee"),
                         labels_for("uk")) is None


# --- 4. my-payslips: object-scoped self-service -------------------------------

def test_my_payslips_scoped_to_own_entity(db, client, company):
    me = Entity(name="Dana R", type="employee", **BANK)
    other = Entity(name="Someone Else", type="employee")
    db.add_all([me, other])
    db.flush()
    _mk_run(db, entity_id=me.id, name="Dana R", net=2_000_000)
    _mk_run(db, entity_id=other.id, name="Someone Else", net=9_000_000)
    db.commit()

    api = _api(client, company, role="employee", entity_id=str(me.id), admin=False)
    r = api.get("/payroll/my-payslips")
    assert r.status_code == 200, r.text
    slips = r.json()["payslips"]
    assert len(slips) == 1
    assert slips[0]["net_pay"] == 2_000_000
    assert slips[0]["entity_id"] == str(me.id)


def test_my_payslips_unlinked_user_empty(db, client, company):
    api = _api(client, company, role="employee", entity_id=None, admin=False)
    r = api.get("/payroll/my-payslips")
    assert r.status_code == 200
    assert r.json()["payslips"] == [] and r.json()["entity_id"] is None
