"""Credit notes the way the books expect them (deep browser test, 2026-10-02,
finding #44: a paid invoice couldn't be credited, and a credited invoice read
"partially paid").

A credit note may go against any issued invoice, paid or not, up to what is
left to credit. It reverses its share of the revenue (or expense) and of the
VAT; it first settles what the invoice still owes, and the rest becomes the
party's credit — refunded now or later, or used on another invoice."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.invoices import (
    _to_read,
    add_credit_note,
    add_payment,
    apply_credit,
    create_invoice,
    refund_credit,
    void_invoice,
)
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.schemas.invoice import (
    CreditApply,
    CreditNoteCreate,
    CreditRefund,
    InvoiceCreate,
    InvoiceItemCreate,
    PaymentCreate,
)
from tests.test_ar_ap_payments import _bal, _balanced, ir, uk  # noqa: F401 — the books fixtures

AR, RETURNS, CREDIT, VAT_OUT, BANK = "1100", "4100", "2150", "2200", "1200"
AP, ADVANCE = "2100", "1500"


def _party(db, kind="client"):
    ent = Entity(type=kind, name=f"{kind} {uuid.uuid4().hex[:5]}")
    db.add(ent)
    db.commit()
    return ent


def _invoice(db, *, kind="sales", net=1000, vat_rate=20.0, entity=None, items=True, amount=None):
    today = date.today()
    out = create_invoice(InvoiceCreate(
        number=f"CN-{uuid.uuid4().hex[:6]}", kind=kind, issue_date=today, due_date=today + timedelta(days=30),
        amount=amount if amount is not None else 0, currency="GBP", status="issued",
        entity_id=entity.id if entity else None,
        items=[InvoiceItemCreate(product_name="Workshop", quantity=1, unit_price=net, tax_rate=vat_rate)] if items else [],
    ), db)
    return out


def _read(db, inv_id):
    return _to_read(db.get(Invoice, inv_id))


def test_a_paid_invoice_can_be_credited_and_its_vat_goes_back_too(uk):
    inv = _invoice(uk, net=1000, vat_rate=20.0)                       # 1,200 with VAT
    add_payment(inv.id, PaymentCreate(amount=1200), uk)
    note = add_credit_note(inv.id, CreditNoteCreate(amount=600, reason="half returned"), uk)
    assert note.note_type == "reduction" and note.amount == 600
    row = _read(uk, inv.id)
    assert row.status == "paid" and row.balance_due == 0
    assert row.credited == 600 and row.credit_available == 600       # the customer's credit, from this invoice
    assert _bal(uk, RETURNS) == 500 and _bal(uk, VAT_OUT) == -(200 - 100)   # 500 net + 100 of the 200 VAT back
    assert _bal(uk, AR) == 0 and _bal(uk, CREDIT) == -600 and _balanced(uk)


def test_a_credit_note_settles_what_is_owed_first(uk):
    inv = _invoice(uk, net=1000, vat_rate=0.0)
    add_payment(inv.id, PaymentCreate(amount=400), uk)                # 600 still owed
    add_credit_note(inv.id, CreditNoteCreate(amount=700), uk)
    row = _read(uk, inv.id)
    assert row.balance_due == 0 and row.status == "paid" and row.credit_available == 100
    assert _bal(uk, AR) == 0 and _bal(uk, CREDIT) == -100 and _balanced(uk)


def test_a_credit_note_is_not_a_payment(uk):
    part = _invoice(uk, net=1000, vat_rate=0.0)
    add_credit_note(part.id, CreditNoteCreate(amount=300), uk)
    assert _read(uk, part.id).status == "issued"                      # was "partially_paid"
    whole = _invoice(uk, net=500, vat_rate=0.0)
    add_credit_note(whole.id, CreditNoteCreate(amount=500), uk)
    row = _read(uk, whole.id)
    assert row.status == "credited" and row.balance_due == 0 and row.credit_available == 0


def test_no_more_than_is_left_to_credit_and_never_a_void_invoice(uk):
    inv = _invoice(uk, net=1000, vat_rate=0.0)
    add_payment(inv.id, PaymentCreate(amount=1000), uk)
    add_credit_note(inv.id, CreditNoteCreate(amount=800), uk)
    with pytest.raises(HTTPException) as ei:
        add_credit_note(inv.id, CreditNoteCreate(amount=300), uk)
    assert ei.value.status_code == 400 and "exceeds what is left to credit (200)" in ei.value.detail
    other = _invoice(uk, net=500, vat_rate=0.0)
    void_invoice(other.id, uk)
    with pytest.raises(HTTPException) as ei:
        add_credit_note(other.id, CreditNoteCreate(amount=100), uk)
    assert ei.value.status_code == 409


def test_refund_now_pays_the_credit_back_from_the_bank(uk):
    inv = _invoice(uk, net=1000, vat_rate=0.0)
    add_payment(inv.id, PaymentCreate(amount=1000), uk)
    bank_after_payment = _bal(uk, BANK)
    add_credit_note(inv.id, CreditNoteCreate(amount=250, refund=True), uk)
    row = _read(uk, inv.id)
    assert row.credit_available == 0 and _bal(uk, CREDIT) == 0
    assert _bal(uk, BANK) == bank_after_payment - 250 and _balanced(uk)


def test_a_credit_is_refunded_later_in_part_then_in_full(uk):
    inv = _invoice(uk, net=1000, vat_rate=0.0)
    add_payment(inv.id, PaymentCreate(amount=1000), uk)
    add_credit_note(inv.id, CreditNoteCreate(amount=400), uk)
    assert refund_credit(inv.id, CreditRefund(amount=150), uk).credit_available == 250
    with pytest.raises(HTTPException) as ei:
        refund_credit(inv.id, CreditRefund(amount=300), uk)
    assert ei.value.status_code == 400
    assert refund_credit(inv.id, CreditRefund(), uk).credit_available == 0     # the rest
    with pytest.raises(HTTPException) as ei:
        refund_credit(inv.id, CreditRefund(), uk)
    assert ei.value.status_code == 409
    assert _bal(uk, CREDIT) == 0 and _balanced(uk)


def test_a_customers_credit_pays_their_next_invoice_without_money_moving(uk):
    client = _party(uk)
    first = _invoice(uk, net=1000, vat_rate=0.0, entity=client)
    add_payment(first.id, PaymentCreate(amount=1000), uk)
    add_credit_note(first.id, CreditNoteCreate(amount=600), uk)
    second = _invoice(uk, net=1000, vat_rate=0.0, entity=client)
    assert _read(uk, second.id).party_credit == 600
    bank_before = _bal(uk, BANK)
    pay = apply_credit(second.id, CreditApply(), uk)
    assert pay.method == "credit" and pay.amount == 600
    row = _read(uk, second.id)
    assert row.status == "partially_paid" and row.balance_due == 400 and row.party_credit == 0
    assert _read(uk, first.id).credit_available == 0
    assert _bal(uk, BANK) == bank_before and _bal(uk, CREDIT) == 0 and _bal(uk, AR) == 400 and _balanced(uk)
    with pytest.raises(HTTPException) as ei:
        apply_credit(second.id, CreditApply(), uk)
    assert ei.value.status_code == 409                                  # no credit left


def test_an_overpayment_is_credit_that_can_be_refunded_or_used(uk):
    client = _party(uk)
    first = _invoice(uk, net=1000, vat_rate=0.0, entity=client)
    add_payment(first.id, PaymentCreate(amount=1300), uk)
    assert _read(uk, first.id).credit_available == 300
    second = _invoice(uk, net=200, vat_rate=0.0, entity=client)
    apply_credit(second.id, CreditApply(), uk)
    assert _read(uk, second.id).status == "paid"
    assert _read(uk, first.id).credit_available == 100
    refund_credit(first.id, CreditRefund(), uk)
    assert _read(uk, first.id).credit_available == 0 and _bal(uk, CREDIT) == 0 and _balanced(uk)


def test_a_paid_bill_credited_leaves_the_supplier_owing_us(uk):
    supplier = _party(uk, "supplier")
    bill = _invoice(uk, kind="purchase", net=800, vat_rate=0.0, entity=supplier)
    add_payment(bill.id, PaymentCreate(amount=800), uk)
    add_credit_note(bill.id, CreditNoteCreate(amount=300), uk)
    assert _bal(uk, AP) == 0 and _bal(uk, ADVANCE) == 300 and _balanced(uk)
    bank_before = _bal(uk, BANK)
    refund_credit(bill.id, CreditRefund(), uk)                          # the supplier pays us back
    assert _bal(uk, BANK) == bank_before + 300 and _bal(uk, ADVANCE) == 0 and _balanced(uk)


def test_the_vat_return_and_the_ledger_agree_on_a_credit_note(uk):
    """The return took the credit note's VAT share off box 1; now the ledger's
    output VAT account drops by the same amount."""
    from app.services.uk_mtd.periods import VatPeriod
    from app.services.uk_mtd.vat import vat_return
    inv = _invoice(uk, net=1000, vat_rate=20.0)
    add_payment(inv.id, PaymentCreate(amount=1200), uk)
    add_credit_note(inv.id, CreditNoteCreate(amount=600), uk)
    today = date.today()
    boxes = vat_return(uk, VatPeriod(start=today - timedelta(days=40), end=today + timedelta(days=40)), currency="GBP")["boxes"]
    assert boxes["1"] == 200 - 100 == -_bal(uk, VAT_OUT)


def test_the_history_names_each_step_with_its_values(uk):
    """The history went to the page as English sentences ("Credit note 600 GBP.");
    each event now carries its values for the page to word."""
    from app.api.invoices import invoice_timeline
    client = _party(uk)
    first = _invoice(uk, net=1000, vat_rate=0.0, entity=client)
    add_payment(first.id, PaymentCreate(amount=1000), uk)
    add_credit_note(first.id, CreditNoteCreate(amount=500, reason="returned"), uk)
    refund_credit(first.id, CreditRefund(amount=200), uk)
    second = _invoice(uk, net=1000, vat_rate=0.0, entity=client)
    apply_credit(second.id, CreditApply(), uk)
    events = {e.event: e.params for e in invoice_timeline(first.id, uk)}
    assert events["created"] == {"number": first.number}
    assert events["payment"] == {"amount": 1000, "currency": "GBP", "direction": "in", "method": "bank"}
    assert events["credit_note"]["amount"] == 500 and events["credit_note"]["reason"] == "returned"
    assert events["credit"]["amount"] == 500 and events["refund"]["amount"] == 200
    assert events["credit_used"] == {"amount": 300, "currency": "GBP", "number": second.number}
    second_events = [e.event for e in invoice_timeline(second.id, uk)]
    assert second_events.count("payment") == 1 and "credit_used" not in second_events
    assert {e.params["method"] for e in invoice_timeline(second.id, uk) if e.event == "payment"} == {"credit"}
