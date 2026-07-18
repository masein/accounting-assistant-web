"""Shareholder-equity postings: balanced GL, per-shareholder تفضیلی attribution,
cap-table dividend allocation, registered-capital tracking."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from app.models.equity import EquityEvent, Shareholding
from app.models.transaction import TransactionLine
from app.services import equity_service as eq


@pytest.fixture(autouse=True)
def company(db):
    """Every equity test runs inside a tenant company so registered-capital
    tracking works; the resolver self-heals the equity accounts under it. The
    service commits internally, so at teardown we purge everything stamped with
    this company (incl. self-healed duplicate accounts) to keep the shared test
    DB clean for unscoped tests."""
    c = Company(id=uuid.uuid4(), name="EqCo", slug=f"eq-{uuid.uuid4().hex[:8]}",
                locale="ir", base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.flush()
    try:
        with use_company(c.id):
            yield c
    finally:
        _purge_company(db, c.id)


def _purge_company(db, company_id):
    from app.db.tenant import tenant_bypass
    from app.models.account import Account
    from app.models.ai_accountant import AIProposal
    from app.models.audit_log import AuditLog
    from app.models.transaction import Transaction, TransactionLine
    db.rollback()
    with tenant_bypass():
        # FK-safe order: children before parents.
        for Model in (TransactionLine, TransactionEntity, EquityEvent, Shareholding,
                      AIProposal, AuditLog, Transaction, Entity, Account):
            db.query(Model).filter(Model.company_id == company_id).delete(synchronize_session=False)
        db.commit()


def _shareholder(db, name, percent):
    e = Entity(name=name, type="shareholder")
    db.add(e)
    db.flush()
    db.add(Shareholding(entity_id=e.id, percent=percent, since=date(2026, 1, 1)))
    db.flush()
    return e


def _lines_for(db, txn_id):
    rows = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == uuid.UUID(txn_id))).scalars().all()
    return rows


def test_contribution_balances_and_raises_capital(db):
    cyrus = _shareholder(db, "Cyrus", 60)
    res = eq.contribution(db, entity_id=cyrus.id, amount=500_000_000, txn_date=date(2026, 3, 1))
    db.commit()
    lines = _lines_for(db, res.transaction_ids[0])
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 500_000_000
    # shareholder linked (تفضیلی)
    links = db.execute(select(TransactionEntity).where(
        TransactionEntity.transaction_id == uuid.UUID(res.transaction_ids[0]))).scalars().all()
    assert any(l.role == "shareholder" and l.entity_id == cyrus.id for l in links)
    # event tagged + registered capital up
    ev = db.execute(select(EquityEvent).where(EquityEvent.event_type == "contribution")).scalars().first()
    assert ev is not None and ev.amount == 500_000_000
    assert res.registered_capital == 500_000_000


def test_dividend_allocated_60_40_per_shareholder(db):
    cyrus = _shareholder(db, "Cyrus", 60)
    sara = _shareholder(db, "Sara", 40)
    res = eq.declare_dividend(db, total_amount=100_000_000, txn_date=date(2026, 3, 10))
    db.commit()
    by_name = {a["entity_name"]: a["amount"] for a in res.allocations}
    assert by_name == {"Cyrus": 60_000_000, "Sara": 40_000_000}
    # one transaction per shareholder, each balanced
    assert len(res.transaction_ids) == 2
    for tid in res.transaction_ids:
        lines = _lines_for(db, tid)
        assert sum(l.debit for l in lines) == sum(l.credit for l in lines)
    # retained earnings fell by 100m total (sum of debits to retained code)
    from app.services.account_resolver import resolve_account_code
    retained = resolve_account_code(db, "retained_earnings")
    from app.models.account import Account
    racc = db.execute(select(Account).where(Account.code == retained)).scalars().first()
    rlines = db.execute(select(TransactionLine).where(TransactionLine.account_id == racc.id)).scalars().all()
    assert sum(l.debit - l.credit for l in rlines) == 100_000_000  # net debit = reduction


def test_dividend_payment_reduces_payable(db):
    sara = _shareholder(db, "Sara", 100)
    eq.declare_dividend(db, total_amount=50_000_000, txn_date=date(2026, 3, 10))
    res = eq.pay_dividend(db, entity_id=sara.id, amount=50_000_000, txn_date=date(2026, 3, 20))
    db.commit()
    lines = _lines_for(db, res.transaction_ids[0])
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 50_000_000


def test_capital_increase_from_retained(db):
    _shareholder(db, "Cyrus", 100)
    res = eq.capital_increase(db, amount=1_000_000_000, txn_date=date(2026, 3, 15), source="retained_earnings")
    db.commit()
    lines = _lines_for(db, res.transaction_ids[0])
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 1_000_000_000
    assert res.registered_capital == 1_000_000_000


def test_current_account_withdrawal(db):
    sara = _shareholder(db, "Sara", 100)
    res = eq.shareholder_current_account(db, entity_id=sara.id, amount=50_000_000,
                                         txn_date=date(2026, 3, 12), direction="out")
    db.commit()
    lines = _lines_for(db, res.transaction_ids[0])
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == 50_000_000
    ev = db.execute(select(EquityEvent).where(EquityEvent.event_type == "current_account_out")).scalars().first()
    assert ev is not None and ev.entity_id == sara.id


def test_non_shareholder_rejected(db):
    emp = Entity(name="Bob", type="employee")
    db.add(emp)
    db.flush()
    with pytest.raises(eq.EquityError):
        eq.contribution(db, entity_id=emp.id, amount=1000, txn_date=date(2026, 3, 1))


def test_allocation_rounding_sums_to_total(db):
    _shareholder(db, "A", 33.3333)
    _shareholder(db, "B", 33.3333)
    _shareholder(db, "C", 33.3334)
    allocs = eq.allocate_by_cap_table(db, 100_000_000)
    assert sum(a for _, a in allocs) == 100_000_000


def test_changes_in_equity_reflects_dividend_and_capital_increase(db):
    _shareholder(db, "Cyrus", 60)
    _shareholder(db, "Sara", 40)
    eq.declare_dividend(db, total_amount=100_000_000, txn_date=date(2026, 3, 10))
    eq.capital_increase(db, amount=1_000_000_000, txn_date=date(2026, 3, 15), source="retained_earnings")
    db.commit()
    from app.services.reporting.iran_statement_service import build_iran_changes_in_equity
    resp = build_iran_changes_in_equity(db, from_date=date(2026, 1, 1), to_date=date(2026, 12, 31))
    rows = {r.key: r for r in resp.rows}

    def cell(key, comp):
        r = rows[key]
        return next(c.amount for c in r.cells if c.component == comp)

    assert cell("approved_dividends", "eq_retained_earnings") == -100_000_000
    assert cell("capital_increase", "eq_capital") == 1_000_000_000
    assert cell("capital_increase", "eq_retained_earnings") == -1_000_000_000


def test_shareholder_person_ledger(db):
    cyrus = _shareholder(db, "Cyrus", 100)
    eq.contribution(db, entity_id=cyrus.id, amount=500_000_000, txn_date=date(2026, 3, 1))
    eq.declare_dividend(db, total_amount=60_000_000, txn_date=date(2026, 3, 10))
    eq.pay_dividend(db, entity_id=cyrus.id, amount=60_000_000, txn_date=date(2026, 3, 20))
    db.commit()
    from app.services.reporting.operations_report_service import OperationsReportService
    led = OperationsReportService(db).person_running_balance(
        cyrus.id, "shareholder", date(2026, 1, 1), date(2026, 12, 31))
    # claim = +500m capital + 60m dividend declared − 60m paid = 500m
    assert led.closing_balance == 500_000_000
    assert len(led.rows) == 3
