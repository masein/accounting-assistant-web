"""Time-varying & multi-jurisdiction tax (test plan §7.3, §7.6).

Extends the PR-2 VAT engine: effective-dated rate selection across a change
date, tax treatments (zero-rated / exempt / reverse-charge), and tax-summary
bucketing — on UK + Iran seeds, all double-entry balanced. Single-rate
invoices must still behave exactly as before (regression).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.invoices import create_invoice
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.transaction import TransactionLine
from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate
from app.services.locale_service import set_reporting_locale
from app.services.tax_rate_service import seed_tax_rates, tax_rate_for
from app.services.tax_service import compute_tax_summary


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
    seed_tax_rates(db)
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


def _item(name="Widget", qty=1, price=1000, **kw):
    return InvoiceItemCreate(product_name=name, quantity=qty, unit_price=price, **kw)


def _invoice(db, *, issue_date, items, kind="sales", number="INV-1"):
    return create_invoice(InvoiceCreate(
        number=number, kind=kind, issue_date=issue_date, due_date=issue_date,
        amount=0, currency="GBP", items=items,
    ), db)


def _line_for(db, inv_id, code: str) -> tuple[int, int]:
    from uuid import UUID
    acc = db.execute(select(Account).where(Account.code == code)).scalar_one()
    inv_txn = db.execute(
        select(TransactionLine).where(TransactionLine.account_id == acc.id)
    ).scalars().all()
    return sum(li.debit for li in inv_txn), sum(li.credit for li in inv_txn)


# ─── 1. Effective-dated rate selection (§7.6) ──────────────────────────

def test_uk_rate_resolver_across_change_date(uk):
    # UK standard: 17.5% until 2011-01-03, 20% from 2011-01-04.
    assert tax_rate_for(uk, "UK_VAT_STANDARD", date(2010, 6, 1)) == 17.5
    assert tax_rate_for(uk, "UK_VAT_STANDARD", date(2011, 1, 3)) == 17.5
    assert tax_rate_for(uk, "UK_VAT_STANDARD", date(2011, 1, 4)) == 20.0
    assert tax_rate_for(uk, "UK_VAT_STANDARD", date(2020, 1, 1)) == 20.0


def test_ir_rate_resolver_across_change_date(ir):
    assert tax_rate_for(ir, "IR_VAT_STANDARD", date(2018, 1, 1)) == 8.0
    assert tax_rate_for(ir, "IR_VAT_STANDARD", date(2019, 3, 21)) == 9.0


def test_invoice_before_change_uses_old_rate(uk):
    inv = _invoice(uk, issue_date=date(2010, 6, 1),
                   items=[_item(price=1000, tax_code="UK_VAT_STANDARD")])
    it = inv.items[0]
    assert float(it.tax_rate) == 17.5
    # amount = 1000 + 17.5% = 1175
    assert inv.amount == 1175


def test_invoice_after_change_uses_new_rate(uk):
    inv = _invoice(uk, issue_date=date(2012, 6, 1),
                   items=[_item(price=1000, tax_code="UK_VAT_STANDARD")])
    assert float(inv.items[0].tax_rate) == 20.0
    assert inv.amount == 1200


def test_same_code_same_day_consistent(uk):
    a = _invoice(uk, issue_date=date(2012, 6, 1), number="INV-A",
                 items=[_item(price=500, tax_code="UK_VAT_STANDARD")])
    b = _invoice(uk, issue_date=date(2012, 6, 1), number="INV-B",
                 items=[_item(price=500, tax_code="UK_VAT_STANDARD")])
    assert float(a.items[0].tax_rate) == float(b.items[0].tax_rate) == 20.0


# ─── 2. Treatments (§7.3) ──────────────────────────────────────────────

def test_zero_rated_export_charges_no_output_tax(uk):
    inv = _invoice(uk, issue_date=date(2012, 6, 1),
                   items=[_item(price=1000, tax_code="UK_VAT_STANDARD", tax_treatment="zero_rated")])
    assert inv.amount == 1000                      # no VAT added
    assert inv.items[0].taxable is False
    assert inv.items[0].tax_treatment == "zero_rated"
    # No output VAT posted (2200).
    assert _line_for(uk, inv.id, "2200") == (0, 0)


def test_exempt_charges_no_output_tax(uk):
    inv = _invoice(uk, issue_date=date(2012, 6, 1),
                   items=[_item(price=1000, tax_treatment="exempt", tax_rate=20)])
    assert inv.amount == 1000
    assert _line_for(uk, inv.id, "2200") == (0, 0)


def test_reverse_charge_nets_to_zero_and_flagged(uk):
    inv = _invoice(uk, issue_date=date(2012, 6, 1),
                   items=[_item(price=1000, tax_code="UK_VAT_STANDARD", tax_treatment="reverse_charge")])
    # Customer self-accounts: no VAT added to the invoice, none posted.
    assert inv.amount == 1000
    assert _line_for(uk, inv.id, "2200") == (0, 0)
    summary = compute_tax_summary(uk, date(2012, 1, 1), date(2012, 12, 31))
    assert summary["output_tax"] == 0              # nothing in net
    assert summary["reverse_charge_notional"] == 200   # 20% of 1000, labelled
    rc = summary["by_treatment"]["reverse_charge"]
    assert rc["output"] == 200 and rc["input"] == 200  # nets to zero


# ─── 3. Tax-summary bucketing by rate (§7.6) ───────────────────────────

def test_tax_summary_buckets_by_period_rate(uk):
    _invoice(uk, issue_date=date(2010, 6, 1), number="OLD",
             items=[_item(price=1000, tax_code="UK_VAT_STANDARD")])   # 17.5% → 175
    _invoice(uk, issue_date=date(2012, 6, 1), number="NEW",
             items=[_item(price=1000, tax_code="UK_VAT_STANDARD")])   # 20% → 200
    summary = compute_tax_summary(uk, date(2010, 1, 1), date(2013, 12, 31))
    assert summary["output_tax"] == 375
    assert summary["by_rate"]["17.5"]["output"] == 175
    assert summary["by_rate"]["20"]["output"] == 200
    assert set(summary["rates"]) == {17.5, 20.0}


# ─── 4. Regression: single-rate invoice unchanged ──────────────────────

def test_single_rate_invoice_unchanged(uk):
    inv = _invoice(uk, issue_date=date(2024, 6, 1),
                   items=[_item(price=1000, tax_rate=20)])           # explicit rate, no code
    assert inv.amount == 1200
    assert inv.items[0].taxable is True
    assert inv.items[0].tax_treatment == "standard"
    summary = compute_tax_summary(uk, date(2024, 1, 1), date(2024, 12, 31))
    assert summary["output_tax"] == 200
    assert "caveat" in summary and summary["caveat"]


def test_ir_invoice_effective_rate(ir):
    inv = create_invoice(InvoiceCreate(
        number="IR-1", kind="sales", issue_date=date(2020, 6, 1), due_date=date(2020, 6, 1),
        amount=0, currency="IRR", items=[_item(price=1_000_000, tax_code="IR_VAT_STANDARD")],
    ), ir)
    assert float(inv.items[0].tax_rate) == 9.0
    assert inv.amount == 1_090_000
