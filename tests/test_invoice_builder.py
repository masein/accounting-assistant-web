"""Multi-line invoice builder backend: a 3-line invoice computes a tax-inclusive
amount, and the preview-pdf endpoint renders a branded draft WITHOUT persisting
a row or posting a journal."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.invoices import create_invoice, preview_invoice_pdf
from app.db.base import Base
from app.db.seed import UK_SEED_ACCOUNTS, _parent_code_uk
from app.models.account import Account
from app.models.invoice import Invoice
from app.models.transaction import Transaction
from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate
from app.services.locale_service import set_reporting_locale


@pytest.fixture
def uk():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):
        cur = conn.cursor(); cur.execute("PRAGMA foreign_keys=ON"); cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code = {}
    for code, name, level in UK_SEED_ACCOUNTS:
        a = Account(code=code, name=name, level=level); db.add(a); by_code[code] = a
    db.flush()
    for code, _n, _l in UK_SEED_ACCOUNTS:
        p = _parent_code_uk(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, "uk")
    db.commit()
    try:
        yield db
    finally:
        db.close()


def _draft(items):
    today = date.today()
    return InvoiceCreate(
        number=f"INV-{uuid.uuid4().hex[:6]}", kind="sales",
        issue_date=today, due_date=today + timedelta(days=30),
        amount=0, currency="GBP", status="issued",
        items=[InvoiceItemCreate(**it) for it in items],
    )


THREE_LINES = [
    {"product_name": "Consulting", "quantity": 10, "unit_price": 100, "line_total": 1000, "tax_rate": 20},  # +200 tax
    {"product_name": "Design", "quantity": 2, "unit_price": 250, "line_total": 500, "tax_rate": 20},        # +100 tax
    {"product_name": "Export item", "quantity": 1, "unit_price": 400, "line_total": 400,
     "tax_treatment": "zero_rated", "tax_rate": 20},                                                         # no tax
]


def test_three_line_invoice_is_tax_inclusive_and_balanced(uk):
    out = create_invoice(_draft(THREE_LINES), uk)
    # subtotal 1900, tax only on the two standard lines = 300, total 2200.
    assert out.subtotal == 1900
    assert out.tax_total == 300
    assert out.amount == 2200
    # A balanced AR journal was posted (DR 1100 / CR 4000 / CR output tax).
    row = uk.get(Invoice, out.id)
    assert row.transaction_id is not None
    txn = uk.get(Transaction, row.transaction_id)
    debit = sum(int(l.debit) for l in txn.lines)
    credit = sum(int(l.credit) for l in txn.lines)
    assert debit == credit == 2200


def test_preview_pdf_does_not_persist(uk):
    """The preview renders the draft but creates no Invoice and no Transaction."""
    try:
        import weasyprint  # noqa: F401
    except Exception as e:
        pytest.skip(f"weasyprint unavailable: {e}")
    before_inv = len(uk.execute(select(Invoice)).scalars().all())
    before_txn = len(uk.execute(select(Transaction)).scalars().all())
    resp = preview_invoice_pdf(_draft(THREE_LINES), uk)
    assert resp.media_type == "application/pdf"
    assert resp.body[:5] == b"%PDF-"
    # Nothing persisted.
    assert len(uk.execute(select(Invoice)).scalars().all()) == before_inv
    assert len(uk.execute(select(Transaction)).scalars().all()) == before_txn


def test_preview_pdf_single_amount_fallback(uk):
    """A draft with no items (single-amount mode) still previews."""
    try:
        import weasyprint  # noqa: F401
    except Exception as e:
        pytest.skip(f"weasyprint unavailable: {e}")
    today = date.today()
    payload = InvoiceCreate(number="INV-X1", kind="sales", issue_date=today,
                            due_date=today + timedelta(days=30), amount=750, currency="GBP",
                            status="issued", items=[])
    resp = preview_invoice_pdf(payload, uk)
    assert resp.body[:5] == b"%PDF-"
    assert len(uk.execute(select(Invoice)).scalars().all()) == 0
