"""AR/AP depth: recognition at issue, partial payment, overpayment→credit,
credit notes, aging by open balance, locale-aware account resolution.

Each invoice/payment/credit-note posting must keep double-entry balanced.
Tests call the endpoint functions directly with an isolated in-memory chart
(UK + Iran) so seeding one locale can't leak into the shared session fixture.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.invoices import (
    _to_read,
    add_credit_note,
    add_payment,
    create_invoice,
    invoice_timeline,
    mark_invoice_paid,
    update_invoice,
)
from app.models.invoice import Invoice
from app.api.manager_reports import accounts_payable, accounts_receivable
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.transaction import TransactionLine
from app.schemas.invoice import (
    CreditNoteCreate,
    InvoiceCreate,
    InvoiceUpdate,
    MarkInvoicePaidRequest,
    PaymentCreate,
)
from app.services.locale_service import set_reporting_locale
from fastapi import HTTPException


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _balanced(db: Session) -> bool:
    dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0))).scalar() or 0
    cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0))).scalar() or 0
    return int(dr) == int(cr)


def _bal(db: Session, code: str) -> int:
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one()
    dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0)).where(TransactionLine.account_id == acc.id)).scalar() or 0
    cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0)).where(TransactionLine.account_id == acc.id)).scalar() or 0
    return int(dr) - int(cr)


def _issue(db, *, kind="sales", amount=1000, currency="GBP", days_due=30):
    today = date.today()
    out = create_invoice(InvoiceCreate(
        number=f"INV-{uuid.uuid4().hex[:6]}", kind=kind,
        issue_date=today, due_date=today + timedelta(days=days_due),
        amount=amount, currency=currency, status="issued",
    ), db)
    return out


def _read(db, inv_id):
    db.expire_all()
    return _to_read(db.get(Invoice, inv_id))


class TestRecognition:
    def test_sales_issue_posts_ar_and_revenue_uk(self, uk):
        _issue(uk, kind="sales", amount=1000)
        assert _balanced(uk)
        assert _bal(uk, "1100") == 1000
        assert _bal(uk, "4000") == -1000

    def test_purchase_issue_posts_expense_and_ap_uk(self, uk):
        _issue(uk, kind="purchase", amount=800)
        assert _balanced(uk)
        assert _bal(uk, "5000") == 800
        assert _bal(uk, "2100") == -800

    def test_sales_issue_iran_locale(self, ir):
        _issue(ir, kind="sales", amount=5_000_000, currency="IRR")
        assert _balanced(ir)
        assert _bal(ir, "1112") == 5_000_000
        assert _bal(ir, "4110") == -5_000_000


class TestPartialPayment:
    def test_partial_then_full(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        add_payment(inv.id, PaymentCreate(amount=400), uk)
        row = _read(uk, inv.id)
        assert row.amount_paid == 400 and row.balance_due == 600
        assert row.status == "partially_paid"
        assert _bal(uk, "1100") == 600 and _balanced(uk)
        add_payment(inv.id, PaymentCreate(amount=600), uk)
        row = _read(uk, inv.id)
        assert row.balance_due == 0 and row.status == "paid"
        assert _bal(uk, "1100") == 0 and _balanced(uk)

    def test_currency_mismatch_rejected(self, uk):
        inv = _issue(uk, kind="sales", amount=1000, currency="GBP")
        with pytest.raises(HTTPException) as ei:
            add_payment(inv.id, PaymentCreate(amount=100, currency="USD"), uk)
        assert ei.value.status_code == 400


class TestOverpayment:
    def test_overpay_caps_and_records_credit(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        add_payment(inv.id, PaymentCreate(amount=1200), uk)
        row = _read(uk, inv.id)
        assert row.status == "paid" and row.balance_due == 0   # never negative
        assert _bal(uk, "1100") == 0                            # AR settled
        assert _bal(uk, "2150") == -200                         # customer credit (liability)
        assert _bal(uk, "1200") == 1200                         # full cash in
        assert _balanced(uk)

    def test_purchase_overpay_uses_supplier_advance(self, uk):
        inv = _issue(uk, kind="purchase", amount=800)
        add_payment(inv.id, PaymentCreate(amount=1000), uk)
        row = _read(uk, inv.id)
        assert row.status == "paid" and row.balance_due == 0
        assert _bal(uk, "2100") == 0       # AP settled
        assert _bal(uk, "1500") == 200     # supplier advance (asset) DR
        assert _balanced(uk)


class TestCreditNote:
    def test_sales_credit_note_reduces_ar(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        note = add_credit_note(inv.id, CreditNoteCreate(amount=250, reason="return"), uk)
        assert str(note.invoice_id) == str(inv.id) and note.note_type == "reduction"
        row = _read(uk, inv.id)
        assert row.credited == 250 and row.balance_due == 750
        assert _bal(uk, "1100") == 750     # AR reduced
        assert _bal(uk, "4100") == 250     # sales returns (contra-revenue) DR
        assert _balanced(uk)

    def test_credit_note_cannot_exceed_balance(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        with pytest.raises(HTTPException) as ei:
            add_credit_note(inv.id, CreditNoteCreate(amount=2000), uk)
        assert ei.value.status_code == 400

    def test_credit_note_then_payment_settles(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        add_credit_note(inv.id, CreditNoteCreate(amount=200), uk)
        add_payment(inv.id, PaymentCreate(amount=800), uk)
        row = _read(uk, inv.id)
        assert row.balance_due == 0 and row.status == "paid"
        assert _balanced(uk)

    def test_purchase_credit_note_reduces_ap(self, uk):
        inv = _issue(uk, kind="purchase", amount=800)
        add_credit_note(inv.id, CreditNoteCreate(amount=300), uk)
        row = _read(uk, inv.id)
        assert row.balance_due == 500
        assert _bal(uk, "2100") == -500    # AP reduced
        assert _balanced(uk)


class TestAging:
    def test_ar_aging_uses_open_balance(self, uk):
        inv = _issue(uk, kind="sales", amount=1000, days_due=-5)
        add_payment(inv.id, PaymentCreate(amount=400), uk)
        data = accounts_receivable(None, None, uk)
        item = next(i for i in data["items"] if i["invoice_id"] == str(inv.id))
        assert item["balance_due"] == 600
        assert data["total"] == 600

    def test_ap_aging_shows_bill(self, uk):
        inv = _issue(uk, kind="purchase", amount=800)
        data = accounts_payable(None, None, uk)
        item = next(i for i in data["items"] if i["invoice_id"] == str(inv.id))
        assert item["balance_due"] == 800

    def test_paid_invoice_excluded_from_aging(self, uk):
        inv = _issue(uk, kind="sales", amount=500)
        add_payment(inv.id, PaymentCreate(amount=500), uk)
        data = accounts_receivable(None, None, uk)
        assert all(i["invoice_id"] != str(inv.id) for i in data["items"])


class TestScheduledPayment:
    def test_scheduled_date_moves_no_money(self, uk):
        inv = _issue(uk, kind="purchase", amount=800)
        dr_before = uk.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0))).scalar()
        sched = date.today() + timedelta(days=14)
        out = update_invoice(inv.id, InvoiceUpdate(scheduled_payment_date=sched), uk)
        assert out.scheduled_payment_date == sched
        dr_after = uk.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0))).scalar()
        assert dr_before == dr_after


class TestMarkPaid:
    def test_mark_paid_settles_full_balance(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        out = mark_invoice_paid(inv.id, MarkInvoicePaidRequest(payment_date=date.today()), uk)
        assert out.status == "paid" and out.balance_due == 0
        assert _bal(uk, "1100") == 0 and _bal(uk, "1200") == 1000
        assert _balanced(uk)


class TestLocaleResolution:
    def test_resolver_uk(self, uk):
        from app.services.account_resolver import resolve_account_code
        assert resolve_account_code(uk, "ar") == "1100"
        assert resolve_account_code(uk, "ap") == "2100"
        assert resolve_account_code(uk, "bank") == "1200"

    def test_resolver_iran(self, ir):
        from app.services.account_resolver import resolve_account_code
        assert resolve_account_code(ir, "ar") == "1112"
        assert resolve_account_code(ir, "ap") == "2110"
        assert resolve_account_code(ir, "bank") == "1110"


class TestTimeline:
    def test_timeline_has_issue_and_payment(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        add_payment(inv.id, PaymentCreate(amount=1000), uk)
        events = invoice_timeline(inv.id, uk)
        kinds = {e.event for e in events}
        assert "issued" in kinds and "payment" in kinds
