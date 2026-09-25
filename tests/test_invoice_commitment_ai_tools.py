"""AI tools for invoices, cheques and installments (roadmap 2026-09 §5.1).
Read tools answer from the same totals the UI shows; proposals go through
execute_proposal and the same invoice / commitment code the UI uses."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.models.audit_log import AuditLog
from app.models.commitment import Commitment
from app.models.credit_note import CreditNote
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.commitment_tools import (
    ListCommitments, ListCommitmentsInput, ProposeBounceCheque, ProposeBounceChequeInput,
    ProposeCreateCheque, ProposeCreateChequeInput, ProposeSettleCommitment, ProposeSettleCommitmentInput,
)
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.invoice_tools import (
    GetInvoice, GetInvoiceInput, ListInvoices, ListInvoicesInput,
    ProposeCreateInvoice, ProposeCreateInvoiceInput, ProposeRecordInvoicePayment, ProposeRecordInvoicePaymentInput,
)


def _run(tool, ctx, args):
    return asyncio.run(tool.run(ctx, args))


@pytest.fixture()
def world(auth_client, db):
    ent = auth_client.post("/entities", json={"type": "client", "name": f"AI Client {uuid.uuid4().hex[:6]}"}).json()
    sup = auth_client.post("/entities", json={"type": "supplier", "name": f"AI Supplier {uuid.uuid4().hex[:6]}"}).json()
    number = f"AI-{uuid.uuid4().hex[:6]}"
    r = auth_client.post("/invoices", json={"number": number, "kind": "sales", "status": "issued",
                                            "issue_date": "2026-05-01", "due_date": (date.today() - timedelta(days=10)).isoformat(),
                                            "amount": 3_000_000, "currency": "IRR", "entity_id": ent["id"]})
    assert r.status_code == 201, r.text
    ctx = ToolContext(db=db, user_id="u1", username="tester", user_message="test")
    yield {"client": ent, "supplier": sup, "number": number, "invoice_id": r.json()["id"], "ctx": ctx}
    db.rollback()
    ids = [i for (i,) in db.execute(select(Invoice.id).where(Invoice.entity_id.in_([uuid.UUID(ent["id"]), uuid.UUID(sup["id"])]))).all()]
    for row in db.execute(select(Payment).where(Payment.invoice_id.in_(ids))).scalars().all():
        db.delete(row)
    for row in db.execute(select(CreditNote).where((CreditNote.invoice_id.in_(ids)) | (CreditNote.entity_id.in_([uuid.UUID(ent["id"]), uuid.UUID(sup["id"])])))).scalars().all():
        db.delete(row)
    for row in db.execute(select(Invoice).where(Invoice.id.in_(ids))).scalars().all():
        db.delete(row)
    db.commit()


def test_list_and_get_invoices(world):
    ctx, number = world["ctx"], world["number"]
    out = _run(ListInvoices(), ctx, ListInvoicesInput(overdue_only=True))
    mine = next(i for i in out["invoices"] if i["number"] == number)
    assert mine["balance_due"] == 3_000_000 and mine["overdue_days"] >= 10 and mine["party"] == world["client"]["name"]
    assert out["totals_by_currency"]["IRR"]["overdue_balance"] >= 3_000_000
    by_party = _run(ListInvoices(), ctx, ListInvoicesInput(party=world["client"]["name"]))
    assert [i["number"] for i in by_party["invoices"]] == [number]
    one = _run(GetInvoice(), ctx, GetInvoiceInput(invoice=number.lower()))
    assert one["id"] == world["invoice_id"] and one["payments"] == []
    with pytest.raises(ToolError):
        _run(GetInvoice(), ctx, GetInvoiceInput(invoice="no-such-invoice-xyz"))


def test_record_payment_proposal_executes_through_the_invoice_code(world, db):
    ctx, number = world["ctx"], world["number"]
    card = _run(ProposeRecordInvoicePayment(), ctx, ProposeRecordInvoicePaymentInput(invoice=number, amount=1_000_000, date=date(2026, 6, 5)))
    assert card["status"] == "pending" and "2,000,000" in card["summary"]  # balance after
    res = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id="u1", actor_username="tester")
    db.commit()
    inv = _run(GetInvoice(), ctx, GetInvoiceInput(invoice=number))
    assert inv["status"] == "partially_paid" and inv["amount_paid"] == 1_000_000 and inv["balance_due"] == 2_000_000
    assert len(inv["payments"]) == 1 and res.transaction_id
    audit = db.get(AuditLog, uuid.UUID(res.audit_log_id))
    assert audit.actor_source == "ai-assistant" and audit.entity_type == "payment"
    # over-payment is announced on the card
    over = _run(ProposeRecordInvoicePayment(), ctx, ProposeRecordInvoicePaymentInput(invoice=number, amount=2_500_000))
    assert "customer credit" in over["summary"]


def test_create_invoice_proposal(world, db):
    ctx = world["ctx"]
    card = _run(ProposeCreateInvoice(), ctx, ProposeCreateInvoiceInput(
        kind="sales", party=world["client"]["name"], issue_date=date(2026, 6, 1),
        lines=[{"description": "Consulting", "quantity": 2, "unit_price": 1_500_000, "tax_rate": 10}]))
    assert card["preview"]["subtotal"] == 3_000_000 and card["preview"]["tax"] == 300_000
    res = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id="u1", actor_username="tester")
    db.commit()
    inv = _run(GetInvoice(), ctx, GetInvoiceInput(invoice=card["preview"]["number"]))
    assert inv["status"] == "issued" and inv["amount"] == 3_300_000 and inv["due_date"] == "2026-07-01"
    assert res.transaction_id  # AR recognised
    with pytest.raises(ToolError):
        _run(ProposeCreateInvoice(), ctx, ProposeCreateInvoiceInput(kind="sales", party="nobody-here", lines=[{"description": "x", "unit_price": 1}]))


def test_cheque_lifecycle_through_the_tools(world, db):
    ctx = world["ctx"]
    due = date.today() + timedelta(days=7)
    ref = f"CHQ-{uuid.uuid4().hex[:6]}"
    card = _run(ProposeCreateCheque(), ctx, ProposeCreateChequeInput(title=f"Rent cheque {ref}", amount=800_000, due_date=due, reference=ref, counter_account_code="6112"))
    execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id="u1", actor_username="tester"); db.commit()
    listing = _run(ListCommitments(), ctx, ListCommitmentsInput(kind="cheque", days_ahead=30))
    mine = next(i for i in listing["items"] if i["reference"] == ref)
    assert mine["status"] == "pending" and mine["days_until_due"] == 7 and listing["payable"] >= 800_000

    bounce = _run(ProposeBounceCheque(), ctx, ProposeBounceChequeInput(cheque=ref))
    execute_proposal(db, confirmation_token=bounce["confirmation_token"], actor_user_id="u1", actor_username="tester"); db.commit()
    db.expire_all()
    row = db.get(Commitment, uuid.UUID(mine["id"]))
    assert row.status == "bounced"

    settle = _run(ProposeSettleCommitment(), ctx, ProposeSettleCommitmentInput(commitment=ref, date=date.today()))
    res = execute_proposal(db, confirmation_token=settle["confirmation_token"], actor_user_id="u1", actor_username="tester"); db.commit()
    db.expire_all()
    row = db.get(Commitment, uuid.UUID(mine["id"]))
    assert row.status == "settled" and row.settled_transaction_id is not None and res.transaction_id
    with pytest.raises(ToolError):  # already settled
        _run(ProposeSettleCommitment(), ctx, ProposeSettleCommitmentInput(commitment=ref))


def test_bounce_refuses_installments(world, db, auth_client):
    r = auth_client.post("/commitments/installments", json={"title": f"Loan {uuid.uuid4().hex[:4]}", "total_amount": 300, "count": 3,
                                                             "first_due": (date.today() + timedelta(days=3)).isoformat()})
    assert r.status_code == 201, r.text
    first = r.json()[0]
    with pytest.raises(ToolError):
        _run(ProposeBounceCheque(), world["ctx"], ProposeBounceChequeInput(cheque=first["id"]))


def test_personal_registry_gets_commitments_but_not_cheque_admin():
    from app.services.ai_accountant.orchestrator import build_default_registry, build_personal_registry
    personal = {t.name for t in build_personal_registry()}
    assert {"list_commitments", "propose_settle_commitment"} <= personal
    assert not ({"propose_bounce_cheque", "propose_create_cheque", "list_invoices"} & personal)
    assert {"list_invoices", "get_invoice", "propose_record_invoice_payment", "propose_create_invoice",
            "list_commitments", "propose_settle_commitment", "propose_bounce_cheque", "propose_create_cheque"} <= {t.name for t in build_default_registry()}
