"""Invoice lifecycle over HTTP (roadmap 2026-09 §6, suite 3): issue → partial
payment → paid → over-payment as credit → receipt/invoice PDFs → payment
reversal → void, with the ledger balanced after every step; plus the two
guards this suite added: no payments on a voided invoice, no deleting an
issued one."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.transaction import Transaction, TransactionLine


def _txn_ids(db) -> set:
    return set(db.execute(select(Transaction.id)).scalars().all())


def _each_balanced(db, ids) -> None:
    for tid in ids:
        dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0)).where(TransactionLine.transaction_id == tid)).scalar()
        cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0)).where(TransactionLine.transaction_id == tid)).scalar()
        assert int(dr) == int(cr) and int(dr) > 0, tid


@pytest.fixture()
def client_entity(auth_client, db):
    ent = auth_client.post("/entities", json={"type": "client", "name": f"Lifecycle {uuid.uuid4().hex[:6]}"}).json()
    yield ent
    # The suite shares one SQLite database. Invoices, payments and credit notes
    # hold foreign keys to transactions, and another module wipes transactions
    # wholesale to reseed a chart — so remove what this test created.
    from app.models.credit_note import CreditNote
    from app.models.invoice import Invoice
    from app.models.payment import Payment
    db.rollback()
    inv_ids = [i for (i,) in db.execute(select(Invoice.id).where(Invoice.entity_id == uuid.UUID(ent["id"]))).all()]
    ent_id = uuid.UUID(ent["id"])
    for row in db.execute(select(Payment).where(Payment.invoice_id.in_(inv_ids))).scalars().all():
        db.delete(row)
    # over-payment credits are standalone (no invoice_id) but carry the entity
    for row in db.execute(select(CreditNote).where(
            (CreditNote.invoice_id.in_(inv_ids)) | (CreditNote.entity_id == ent_id))).scalars().all():
        db.delete(row)
    for row in db.execute(select(Invoice).where(Invoice.id.in_(inv_ids))).scalars().all():
        db.delete(row)
    db.commit()


def _issue(auth_client, entity_id, amount=3_000_000, status="issued"):
    r = auth_client.post("/invoices", json={
        "number": f"LC-{uuid.uuid4().hex[:6]}", "kind": "sales", "status": status,
        "issue_date": "2026-06-01", "due_date": "2026-06-30", "amount": amount, "currency": "IRR",
        "entity_id": entity_id,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_issue_pay_overpay_and_documents(auth_client, db, client_entity):
    before = _txn_ids(db)
    inv = _issue(auth_client, client_entity["id"])
    assert inv["status"] == "issued" and inv["balance_due"] == 3_000_000

    p1 = auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 1_000_000, "date": "2026-06-05", "method": "bank"})
    assert p1.status_code == 201, p1.text
    row = auth_client.get("/invoices").json()
    me = next(i for i in row if i["id"] == inv["id"])
    assert me["status"] == "partially_paid" and me["balance_due"] == 2_000_000 and me["amount_paid"] == 1_000_000

    p2 = auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 2_000_000, "date": "2026-06-06", "method": "bank"})
    assert p2.status_code == 201
    me = next(i for i in auth_client.get("/invoices").json() if i["id"] == inv["id"])
    assert me["status"] == "paid" and me["balance_due"] == 0 and me["overpaid"] == 0

    # Over-payment is BY DESIGN booked as customer credit, never as "paid" beyond the invoice.
    p3 = auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 500_000, "date": "2026-06-07", "method": "bank"})
    assert p3.status_code == 201, p3.text
    me = next(i for i in auth_client.get("/invoices").json() if i["id"] == inv["id"])
    assert me["amount_paid"] == 3_000_000 and me["overpaid"] == 500_000 and me["balance_due"] == 0

    payments = auth_client.get(f"/invoices/{inv['id']}/payments").json()
    assert len(payments) == 3

    # every entry this flow created balances
    _each_balanced(db, _txn_ids(db) - before)

    # documents
    receipt = auth_client.get(f"/invoices/{inv['id']}/payments/{payments[0]['id']}/receipt")
    assert receipt.status_code == 200 and receipt.content[:5] == b"%PDF-", receipt.headers.get("content-type")
    pdf = auth_client.get(f"/invoices/{inv['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"

    events = {e["event"] for e in auth_client.get(f"/invoices/{inv['id']}/timeline").json()}
    assert {"created", "issued", "payment"} <= events


def test_reverse_payment_reopens_the_balance(auth_client, db, client_entity):
    inv = _issue(auth_client, client_entity["id"], amount=1_000_000)
    p = auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 1_000_000, "date": "2026-06-05", "method": "bank"}).json()
    before = _txn_ids(db)
    r = auth_client.post(f"/invoices/{inv['id']}/payments/{p['id']}/reverse")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "issued" and r.json()["balance_due"] == 1_000_000 and r.json()["amount_paid"] == 0
    assert auth_client.get(f"/invoices/{inv['id']}/payments").json() == []
    _each_balanced(db, _txn_ids(db) - before)  # the reversal entry itself balances
    assert auth_client.post(f"/invoices/{inv['id']}/payments/{p['id']}/reverse").status_code == 404


def test_void_reverses_everything_and_blocks_further_payments(auth_client, db, client_entity):
    inv = _issue(auth_client, client_entity["id"], amount=2_000_000)
    auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 500_000, "date": "2026-06-05", "method": "bank"})
    before = _txn_ids(db)
    v = auth_client.post(f"/invoices/{inv['id']}/void")
    assert v.status_code == 200 and v.json()["status"] == "voided" and v.json()["balance_due"] == 0
    new = _txn_ids(db) - before
    assert len(new) == 2  # payment reversal + recognition reversal
    _each_balanced(db, new)
    assert auth_client.post(f"/invoices/{inv['id']}/void").status_code == 200  # idempotent
    pay = auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 100, "date": "2026-06-06", "method": "bank"})
    assert pay.status_code == 409
    mp = auth_client.post(f"/invoices/{inv['id']}/mark-paid", json={"payment_date": "2026-06-06", "method": "bank"})
    assert mp.status_code == 409


def test_only_drafts_can_be_deleted(auth_client, db, client_entity):
    issued = _issue(auth_client, client_entity["id"], amount=100_000)
    r = auth_client.delete(f"/invoices/{issued['id']}")
    assert r.status_code == 409 and "Void" in r.json()["detail"]
    draft = _issue(auth_client, client_entity["id"], amount=100_000, status="draft")
    assert auth_client.delete(f"/invoices/{draft['id']}").status_code == 204
    assert auth_client.delete(f"/invoices/{draft['id']}").status_code == 404
    from app.models.audit_log import AuditLog
    acts = db.execute(select(AuditLog.action).where(AuditLog.entity_type == "invoice", AuditLog.entity_id == draft["id"])).scalars().all()
    assert "delete" in acts


def test_double_submit_of_the_same_payment_is_credit_not_double_paid(auth_client, client_entity):
    inv = _issue(auth_client, client_entity["id"], amount=1_000_000)
    body = {"amount": 1_000_000, "date": "2026-06-05", "method": "bank", "reference": "same-ref"}
    assert auth_client.post(f"/invoices/{inv['id']}/payments", json=body).status_code == 201
    assert auth_client.post(f"/invoices/{inv['id']}/payments", json=body).status_code == 201
    me = next(i for i in auth_client.get("/invoices").json() if i["id"] == inv["id"])
    assert me["amount_paid"] == 1_000_000 and me["overpaid"] == 1_000_000 and me["balance_due"] == 0
