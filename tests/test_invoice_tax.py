"""VAT / sales-tax on invoices: per-line tax math, mixed taxable/exempt, the
sales & purchase posting splits (UK + Iran), tax-summary netting, and the
always-present estimate caveat.

Calls endpoint functions directly with an isolated in-memory chart per locale.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.invoices import _to_read, create_invoice
from app.api.reports import get_tax_summary
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.invoice import Invoice
from app.models.transaction import TransactionLine
from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate
from app.services.locale_service import set_reporting_locale
from app.services.tax_service import TAX_CAVEAT, compute_tax_summary


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


def _bal(db: Session, code: str) -> int:
    acc = db.execute(select(Account).where(Account.code == code)).scalars().one()
    dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0)).where(TransactionLine.account_id == acc.id)).scalar() or 0
    cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0)).where(TransactionLine.account_id == acc.id)).scalar() or 0
    return int(dr) - int(cr)


def _balanced(db: Session) -> bool:
    dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0))).scalar() or 0
    cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0))).scalar() or 0
    return int(dr) == int(cr)


def _issue(db, *, kind="sales", currency="GBP", items, number=None, days_due=30):
    today = date.today()
    return create_invoice(InvoiceCreate(
        number=number or f"INV-{uuid.uuid4().hex[:6]}", kind=kind,
        issue_date=today, due_date=today + timedelta(days=days_due),
        amount=0, currency=currency, status="issued",
        items=[InvoiceItemCreate(**it) for it in items],
    ), db)


class TestTaxMath:
    def test_per_line_tax_and_grand_total(self, uk):
        out = _issue(uk, items=[{"product_name": "A", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20}])
        assert out.subtotal == 1000
        assert out.tax_total == 200
        assert out.grand_total == 1700 - 500  # 1200
        assert out.amount == 1200  # amount tracks grand_total

    def test_mixed_taxable_and_exempt(self, uk):
        out = _issue(uk, items=[
            {"product_name": "Taxable", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20, "taxable": True},
            {"product_name": "Exempt", "quantity": 1, "unit_price": 500, "line_total": 500, "tax_rate": 20, "taxable": False},
        ])
        assert out.subtotal == 1500       # both lines
        assert out.tax_total == 200       # only the taxable line
        assert out.grand_total == 1700
        assert out.amount == 1700

    def test_zero_rate_no_tax(self, uk):
        out = _issue(uk, items=[{"product_name": "A", "quantity": 1, "unit_price": 800, "line_total": 800, "tax_rate": 0}])
        assert out.tax_total == 0 and out.grand_total == 800


class TestSalesPostingSplit:
    def test_uk_sales_split(self, uk):
        _issue(uk, kind="sales", items=[
            {"product_name": "Taxable", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20, "taxable": True},
            {"product_name": "Exempt", "quantity": 1, "unit_price": 500, "line_total": 500, "taxable": False},
        ])
        assert _bal(uk, "1100") == 1700    # debtors = grand total
        assert _bal(uk, "4000") == -1500   # revenue = subtotal
        assert _bal(uk, "2200") == -200    # output VAT payable = tax
        assert _balanced(uk)

    def test_iran_sales_split(self, ir):
        _issue(ir, kind="sales", currency="IRR", items=[
            {"product_name": "A", "quantity": 1, "unit_price": 1_000_000, "line_total": 1_000_000, "tax_rate": 10},
        ])
        assert _bal(ir, "1112") == 1_100_000   # AR
        assert _bal(ir, "4110") == -1_000_000  # revenue
        assert _bal(ir, "2130") == -100_000    # output VAT
        assert _balanced(ir)


class TestPurchasePostingSplit:
    def test_uk_purchase_split(self, uk):
        _issue(uk, kind="purchase", items=[
            {"product_name": "Goods", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20},
        ])
        assert _bal(uk, "5000") == 1000    # expense = subtotal
        assert _bal(uk, "1400") == 200     # input VAT recoverable = tax (asset DR)
        assert _bal(uk, "2100") == -1200   # creditors = grand total
        assert _balanced(uk)

    def test_iran_purchase_split(self, ir):
        _issue(ir, kind="purchase", currency="IRR", items=[
            {"product_name": "Goods", "quantity": 1, "unit_price": 2_000_000, "line_total": 2_000_000, "tax_rate": 10},
        ])
        assert _bal(ir, "6112") == 2_000_000
        assert _bal(ir, "1130") == 200_000     # input VAT
        assert _bal(ir, "2110") == -2_200_000  # AP
        assert _balanced(ir)


class TestNoTaxPostingUnchanged:
    def test_no_vat_line_when_zero(self, uk):
        _issue(uk, kind="sales", items=[{"product_name": "A", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 0}])
        assert _bal(uk, "1100") == 1000 and _bal(uk, "4000") == -1000
        assert _bal(uk, "2200") == 0  # no VAT line
        assert _balanced(uk)


class TestTaxSummary:
    def test_output_input_net(self, uk):
        _issue(uk, kind="sales", items=[{"product_name": "S", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20}])
        _issue(uk, kind="purchase", items=[{"product_name": "P", "quantity": 1, "unit_price": 500, "line_total": 500, "tax_rate": 20}])
        today = date.today()
        data = compute_tax_summary(uk, today - timedelta(days=1), today + timedelta(days=1))
        assert data["output_tax"] == 200
        assert data["input_tax"] == 100
        assert data["net_tax"] == 100
        assert data["rates"] == [20]
        assert data["caveat"] == TAX_CAVEAT

    def test_endpoint_defaults_to_quarter_and_has_caveat(self, uk):
        _issue(uk, kind="sales", items=[{"product_name": "S", "quantity": 1, "unit_price": 1000, "line_total": 1000, "tax_rate": 20}])
        data = get_tax_summary(None, None, None, uk)
        assert "caveat" in data and data["caveat"] == TAX_CAVEAT
        assert "assumptions" in data
        # Default period is the current quarter, which contains today's issue.
        assert data["output_tax"] == 200

    def test_caveat_present_even_with_no_tax(self, uk):
        data = compute_tax_summary(uk, date.today() - timedelta(days=1), date.today())
        assert data["caveat"] == TAX_CAVEAT
        assert data["net_tax"] == 0
        assert "No tax rates" in data["assumptions"]
