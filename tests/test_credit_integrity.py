"""Money movements hold together (security review, 2026-10-06; scenarios J1–J5).

- J1 an entry is reversed once: the ledger's reverse route posted a new
  reversal each time it was called, and a void afterwards reversed it again;
- J2 voiding an invoice reverses its credit notes too (their returns, VAT and
  customer credit stayed on the books), and is refused while its credit went
  out as a refund or paid another invoice;
- J3 a reversed use or refund of a credit gives it back, and a credit whose
  own entry was reversed is gone;
- J4 two refunds at once can't both pay out the same credit (PostgreSQL);
- J5 a refund or a payment goes to a bank, cash or cheque account only;
- J6 a cheque that paid more than its invoice and then bounced, or was handed
  back, takes its extra back out of the credit (it stayed refundable, the
  receivable came out too high, and depositing it again made a second credit).
"""
from __future__ import annotations

import threading
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
    reverse_payment,
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
from app.services import credits
from app.services.reporting.ledger_service import LedgerService
from tests.test_ar_ap_payments import _bal, _balanced, uk  # noqa: F401 — the books fixture
from tests.test_cheque_lifecycle import ir  # noqa: F401 — an Iranian company, cheques in notes books

AR, RETURNS, CREDIT, VAT_OUT, BANK, SALES = "1100", "4100", "2150", "2200", "1200", "4000"


def _party(db):
    ent = Entity(type="client", name=f"client {uuid.uuid4().hex[:5]}")
    db.add(ent)
    db.commit()
    return ent


def _invoice(db, *, net=1000, vat_rate=0.0, entity=None):
    today = date.today()
    return create_invoice(InvoiceCreate(
        number=f"CI-{uuid.uuid4().hex[:6]}", kind="sales", issue_date=today, due_date=today + timedelta(days=30),
        amount=0, currency="GBP", status="issued", entity_id=entity.id if entity else None,
        items=[InvoiceItemCreate(product_name="Work", quantity=1, unit_price=net, tax_rate=vat_rate)],
    ), db)


def _left(db, inv_id):
    return _to_read(db.get(Invoice, inv_id)).credit_available


# --- J1 ---------------------------------------------------------------------------------------------------------

def test_an_entry_is_reversed_once_and_a_void_after_it_does_not_reverse_it_again(uk):
    inv = _invoice(uk, net=1000)
    txn_id = uk.get(Invoice, inv.id).transaction_id
    LedgerService(uk).reverse_journal_entry(transaction_id=txn_id)
    assert _bal(uk, AR) == 0
    with pytest.raises(HTTPException) as ei:
        LedgerService(uk).reverse_journal_entry(transaction_id=txn_id)
    assert ei.value.status_code == 409 and ei.value.detail == "This entry has already been reversed."
    void_invoice(inv.id, uk)
    assert _bal(uk, AR) == 0 and _bal(uk, SALES) == 0 and _balanced(uk)       # was -1000: reversed twice


# --- J2 ---------------------------------------------------------------------------------------------------------

def test_a_void_takes_its_credit_note_with_it(uk):
    inv = _invoice(uk, net=1000, vat_rate=20.0)                               # 1,200 with VAT
    add_payment(inv.id, PaymentCreate(amount=1200), uk)
    add_credit_note(inv.id, CreditNoteCreate(amount=600), uk)                # kept as the customer's credit
    bank = _bal(uk, BANK)
    void_invoice(inv.id, uk)
    for code in (AR, RETURNS, CREDIT, VAT_OUT, SALES):
        assert _bal(uk, code) == 0, code                                      # nothing of the invoice left
    assert _bal(uk, BANK) == bank - 1200 and _balanced(uk)                    # its payment reversed too
    assert _left(uk, inv.id) == 0                                              # no credit to refund


def test_no_void_while_its_credit_went_out(uk):
    client = _party(uk)
    first = _invoice(uk, net=1000, entity=client)
    add_payment(first.id, PaymentCreate(amount=1000), uk)
    add_credit_note(first.id, CreditNoteCreate(amount=400), uk)
    refund_credit(first.id, CreditRefund(amount=100), uk)
    with pytest.raises(HTTPException) as ei:
        void_invoice(first.id, uk)
    assert ei.value.status_code == 409 and "Reverse those first" in ei.value.detail
    # used on another invoice counts the same
    client2 = _party(uk)
    other = _invoice(uk, net=1000, entity=client2)
    add_payment(other.id, PaymentCreate(amount=1300), uk)                     # 300 overpaid
    nxt = _invoice(uk, net=200, entity=client2)
    apply_credit(nxt.id, CreditApply(), uk)
    assert credits.consumed(uk, other.id) == 200
    with pytest.raises(HTTPException) as ei:
        void_invoice(other.id, uk)
    assert ei.value.status_code == 409


# --- J3 ---------------------------------------------------------------------------------------------------------

def test_a_reversed_use_gives_the_credit_back(uk):
    client = _party(uk)
    first = _invoice(uk, net=1000, entity=client)
    add_payment(first.id, PaymentCreate(amount=1000), uk)
    add_credit_note(first.id, CreditNoteCreate(amount=600), uk)
    second = _invoice(uk, net=1000, entity=client)
    pay = apply_credit(second.id, CreditApply(), uk)
    assert _left(uk, first.id) == 0 and _bal(uk, CREDIT) == 0
    reverse_payment(second.id, pay.id, uk)
    assert _left(uk, first.id) == 600 and _bal(uk, CREDIT) == -600           # the app and the account agree
    assert _to_read(uk.get(Invoice, second.id)).balance_due == 1000


def test_a_reversed_overpayment_leaves_no_credit(uk):
    inv = _invoice(uk, net=1000)
    pay = add_payment(inv.id, PaymentCreate(amount=1300), uk)
    assert _left(uk, inv.id) == 300
    reverse_payment(inv.id, pay.id, uk)
    assert _left(uk, inv.id) == 0 and _bal(uk, CREDIT) == 0 and _balanced(uk)
    with pytest.raises(HTTPException) as ei:
        refund_credit(inv.id, CreditRefund(), uk)
    assert ei.value.status_code == 409


# --- J5 ---------------------------------------------------------------------------------------------------------

def test_refunds_and_payments_go_to_a_bank_or_cash_account(uk):
    inv = _invoice(uk, net=1000)
    add_payment(inv.id, PaymentCreate(amount=1000), uk)
    add_credit_note(inv.id, CreditNoteCreate(amount=300), uk)
    for code, why in (("3000", "isn't a bank, cash or cheque account"), ("9999", "There is no account")):
        with pytest.raises(HTTPException) as ei:
            refund_credit(inv.id, CreditRefund(bank_account_code=code), uk)
        assert ei.value.status_code == 422 and why in ei.value.detail, (code, ei.value.detail)
    other = _invoice(uk, net=500)
    with pytest.raises(HTTPException) as ei:
        add_payment(other.id, PaymentCreate(amount=500, bank_account_code="3000"), uk)
    assert ei.value.status_code == 422
    assert refund_credit(inv.id, CreditRefund(bank_account_code=BANK), uk).credit_available == 0


# --- J6 (Iranian books: AR 1112, notes receivable 1113, in collection 1114, bank 1110,
#          customer credit 2120, supplier advance 1120, AP 2110, notes payable 2111) ------------------------------

def test_a_bounced_cheque_takes_its_extra_back_and_a_second_deposit_makes_one_credit(ir):
    inv = ir.invoice(amount=10_000_000)
    c = ir.cheque(amount=12_000_000, invoice_id=inv["id"])
    row = ir.inv(inv["id"])
    assert row["status"] == "paid" and row["credit_available"] == 2_000_000 and ir.bal("2120") == -2_000_000
    ir.step(c, "deposit", on="2026-09-10")
    ir.step(c, "bounce", on="2026-09-21")
    row = ir.inv(inv["id"])
    assert row["balance_due"] == 10_000_000 and row["credit_available"] == 0
    assert ir.bal("2120") == 0 and ir.bal("1112") == 10_000_000 and ir.bal("1114") == 0      # AR was 12,000,000
    ir.step(c, "deposit", on="2026-09-22")                                                    # presented again
    row = ir.inv(inv["id"])
    assert row["status"] == "paid" and row["credit_available"] == 2_000_000
    assert ir.bal("2120") == -2_000_000 and ir.bal("1112") == 0                               # one credit, not two
    ir.step(c, "settle", on="2026-09-25")
    assert ir.bal("1110") == 12_000_000 and ir.bal("1114") == 0
    events = [e["event"] for e in ir.api.get(f"/invoices/{inv['id']}/timeline").json()]
    assert "credit_withdrawn" in events and "refund" not in events


def test_no_bounce_while_the_cheques_extra_was_refunded(ir):
    inv = ir.invoice(amount=10_000_000)
    c = ir.cheque(amount=12_000_000, invoice_id=inv["id"])
    r = ir.api.post(f"/invoices/{inv['id']}/refund-credit", json={"amount": 500_000})
    assert r.status_code == 200, r.text
    r = ir.api.post(f"/commitments/{c['id']}/bounce", json={"on": "2026-09-21"})
    assert r.status_code == 409 and "Reverse that first" in r.json()["detail"], r.text
    r = ir.api.post(f"/commitments/{c['id']}/bounce", json={"on": "2026-09-21"}, headers={"X-UI-Language": "fa"})
    assert r.status_code == 409 and "برگشت بزنید" in r.json()["detail"]


def test_an_issued_cheque_that_overpaid_a_bill_bounces_cleanly(ir):
    bill = ir.invoice(kind="purchase", amount=1_000_000)
    c = ir.cheque(direction="pay", amount=1_500_000, invoice_id=bill["id"])
    assert ir.inv(bill["id"])["credit_available"] == 500_000 and ir.bal("1120") == 500_000
    ir.step(c, "bounce", on="2026-09-21")
    row = ir.inv(bill["id"])
    assert row["balance_due"] == 1_000_000 and row["credit_available"] == 0
    assert ir.bal("1120") == 0 and ir.bal("2110") == -1_000_000 and ir.bal("2111") == 0


def test_an_unused_cheque_handed_back_takes_its_extra_back(ir):
    inv = ir.invoice(amount=10_000_000)
    c = ir.cheque(amount=12_000_000, invoice_id=inv["id"])
    ir.step(c, "return", on="2026-09-15")
    row = ir.inv(inv["id"])
    assert row["balance_due"] == 10_000_000 and row["credit_available"] == 0
    assert ir.bal("2120") == 0 and ir.bal("1112") == 10_000_000 and ir.bal("1113") == 0


# --- J4 (PostgreSQL: row locks) ---------------------------------------------------------------------------------

def _pg():
    from tests.conftest import _SQLALCHEMY_TEST_URL
    return _SQLALCHEMY_TEST_URL.startswith("postgresql")


@pytest.mark.skipif(not _pg(), reason="row locks need PostgreSQL")
def test_two_refunds_at_once_pay_the_credit_out_once():
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from app.models.company import Company
    from app.models.credit_note import CreditNote
    from tests.conftest import _TestSession
    from tests.test_admin_audit import _purge_company

    setup = _TestSession()
    c = Company(id=uuid.uuid4(), name="Lock Co", slug=f"lock-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    setup.add(c)
    setup.commit()
    cid = str(c.id)
    try:
        with use_company(cid):
            seed_chart_if_empty(setup, locale="uk")
            setup.commit()
            inv = _invoice(setup, net=1000)
            add_payment(inv.id, PaymentCreate(amount=1000), setup)
            add_credit_note(inv.id, CreditNoteCreate(amount=300), setup)        # 300 of credit
        setup.close()

        first, second = _TestSession(), _TestSession()
        from sqlalchemy import text
        for sess in (first, second):          # a failure waits 5 s at most, never for good
            sess.execute(text("SET lock_timeout = '5s'"))
        outcome: dict = {}

        def other_refund():
            with use_company(cid):
                try:
                    refund_credit(inv.id, CreditRefund(), second)
                    outcome["second"] = "refunded"
                except HTTPException as e:
                    outcome["second"] = e.status_code
                except Exception as e:  # noqa: BLE001 — anything else is the test's finding
                    outcome["second"] = repr(e)[:300]
                finally:
                    second.close()

        t = threading.Thread(target=other_refund)
        try:
            with use_company(cid):
                # the first refund, as the endpoint takes its locks: the invoice, then its credit
                locked = first.get(Invoice, inv.id, with_for_update=True)
                pairs = credits.for_invoice(first, inv.id, lock=True)
                t.start()
                t.join(0.8)
                assert t.is_alive(), "the second refund didn't wait for the first"
                from app.api.invoices import _refund_credits
                _refund_credits(first, locked, pairs, 300, on=date.today(), bank_code=None)
                first.commit()
        finally:
            first.rollback()
            first.close()
        t.join(10)
        assert outcome == {"second": 409}, outcome
        check = _TestSession()
        with use_company(cid):
            refunds = [n.amount for n in check.query(CreditNote).filter(CreditNote.note_type == "refund")]
        check.close()
        assert refunds == [300]
    finally:
        cleanup = _TestSession()
        _purge_company(cleanup, cid)
        cleanup.close()
