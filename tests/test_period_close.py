"""Period close & adjustments (PR 3): accruals with auto-reverse, prepayment
amortization, straight-line depreciation, period lock (API + AI), invoice
void, payment reversal (chargeback), and fiscal-boundary correctness.

Endpoint functions are called directly with an isolated in-memory chart
(UK + Iran) so seeding one locale can't leak into the shared session fixture.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.adjustments import (
    AccrualCreate,
    DepreciationCreate,
    PrepaymentCreate,
    create_accrual,
    create_depreciation,
    create_prepayment,
    get_adjustment,
    release_period,
)
from app.api.invoices import (
    _to_read,
    add_payment,
    create_invoice,
    reverse_payment,
    void_invoice,
)
from app.api.transactions import _create_transaction_from_payload
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.transaction import Transaction, TransactionLine
from app.schemas.invoice import InvoiceCreate, PaymentCreate
from app.schemas.transaction import TransactionCreate, TransactionLineCreate
from app.services.locale_service import set_reporting_locale
from app.services.period_service import set_closed_period


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


# ---------------------------------------------------------------------------
# 1. Accruals with auto-reverse
# ---------------------------------------------------------------------------


class TestAccrual:
    def test_accrual_auto_reverses_next_period(self, uk):
        out = create_accrual(AccrualCreate(
            amount=500, date=date(2026, 3, 31), description="March wages accrual",
            currency="GBP", direction="expense", auto_reverse=True, period_months=1,
        ), uk)
        assert out["transaction_id"] and out["reversal_transaction_id"]
        # Accrual in March: DR expense 5000 / CR accrued liability 2400.
        # Reversal on 1 Apr: opposite. Net across both = 0.
        assert _bal(uk, "5000") == 0      # expense recognised then reversed
        assert _bal(uk, "2400") == 0      # accrued liability nets to zero
        assert _balanced(uk)
        # The reversal is dated to the first day of the next period.
        rev = uk.get(Transaction, uuid.UUID(out["reversal_transaction_id"]))
        assert rev.date == date(2026, 4, 1)

    def test_accrual_without_reverse_stays(self, ir):
        out = create_accrual(AccrualCreate(
            amount=300, date=date(2026, 2, 20), currency="IRR",
            direction="expense", auto_reverse=False,
        ), ir)
        assert out["reversal_transaction_id"] is None
        assert _bal(ir, "6112") == 300     # expense
        assert _bal(ir, "2140") == -300    # accrued liability (credit)
        assert _balanced(ir)

    def test_accrued_income(self, uk):
        create_accrual(AccrualCreate(
            amount=400, date=date(2026, 3, 31), currency="GBP",
            direction="income", auto_reverse=False,
        ), uk)
        assert _bal(uk, "1410") == 400     # accrued income (asset, DR)
        assert _bal(uk, "4000") == -400    # revenue (CR)
        assert _balanced(uk)


# ---------------------------------------------------------------------------
# 2. Prepayment amortization
# ---------------------------------------------------------------------------


class TestPrepayment:
    def test_amortizes_evenly_to_zero(self, uk):
        out = create_prepayment(PrepaymentCreate(
            amount=1200, periods=12, start_date=date(2026, 1, 1),
            description="Annual insurance", currency="GBP",
        ), uk)
        adj_id = uuid.UUID(out["id"])
        assert _bal(uk, "1300") == 1200    # prepaid asset booked
        assert _bal(uk, "1200") == -1200   # bank out
        # Release all 12 periods.
        for _ in range(12):
            out = release_period(adj_id, uk)
        assert out["status"] == "complete"
        assert out["periods_posted"] == 12
        assert _bal(uk, "1300") == 0       # prepaid asset trends to zero
        assert _bal(uk, "5000") == 1200    # released to expense
        assert _balanced(uk)

    def test_uneven_total_reconciles_exactly(self, ir):
        # 1000 / 3 → 333,333,334 split; the sum must equal 1000.
        out = create_prepayment(PrepaymentCreate(
            amount=1000, periods=3, start_date=date(2026, 1, 1), currency="IRR",
        ), ir)
        adj_id = uuid.UUID(out["id"])
        for _ in range(3):
            out = release_period(adj_id, ir)
        assert _bal(ir, "1150") == 0       # prepaid fully released
        assert _bal(ir, "6112") == 1000
        assert _balanced(ir)


# ---------------------------------------------------------------------------
# 3. Straight-line depreciation
# ---------------------------------------------------------------------------


class TestDepreciation:
    def test_straight_line_and_nbv(self, uk):
        out = create_depreciation(DepreciationCreate(
            cost=1200, periods=12, start_date=date(2026, 1, 1), residual=0,
            description="Laptop", currency="GBP",
        ), uk)
        adj_id = uuid.UUID(out["id"])
        assert out["per_period"] == 100
        for _ in range(3):
            out = release_period(adj_id, uk)
        # 3 months: DR depreciation expense 300 / CR accumulated depreciation 300.
        assert _bal(uk, "8500") == 300      # depreciation expense
        assert _bal(uk, "0090") == -300     # accumulated depreciation (contra-asset, credit)
        assert out["net_book_value"] == 900  # cost 1200 − accumulated 300
        assert _balanced(uk)

    def test_residual_excluded_from_depreciation(self, ir):
        out = create_depreciation(DepreciationCreate(
            cost=1000, periods=5, start_date=date(2026, 1, 1), residual=100, currency="IRR",
        ), ir)
        adj_id = uuid.UUID(out["id"])
        for _ in range(5):
            out = release_period(adj_id, ir)
        # Depreciable = 1000 − 100 = 900 over 5 → 180/period.
        assert _bal(ir, "1219") == -900     # accumulated depreciation
        assert _bal(ir, "6120") == 900      # depreciation expense
        assert out["net_book_value"] == 100  # cost − accumulated = residual
        assert _balanced(ir)


# ---------------------------------------------------------------------------
# 4. Period lock
# ---------------------------------------------------------------------------


class TestPeriodLock:
    def test_transaction_api_blocks_closed_period(self, ir):
        set_closed_period(ir, date(2025, 12, 31))
        ir.commit()
        payload = TransactionCreate(
            date=date(2025, 11, 1), description="backdated",
            lines=[
                TransactionLineCreate(account_code="6112", debit=100, credit=0),
                TransactionLineCreate(account_code="1110", debit=0, credit=100),
            ],
        )
        with pytest.raises(HTTPException) as ei:
            _create_transaction_from_payload(ir, payload)
        assert ei.value.status_code == 422

    def test_open_period_after_lock_allowed(self, ir):
        set_closed_period(ir, date(2025, 12, 31))
        ir.commit()
        payload = TransactionCreate(
            date=date(2026, 1, 15), description="ok",
            lines=[
                TransactionLineCreate(account_code="6112", debit=100, credit=0),
                TransactionLineCreate(account_code="1110", debit=0, credit=100),
            ],
        )
        txn = _create_transaction_from_payload(ir, payload)
        assert txn.id is not None

    def test_ai_propose_blocked_in_closed_period(self, ir):
        from app.services.ai_accountant.base import ToolContext, ToolError
        from app.services.ai_accountant.proposal_tools import (
            ProposeCreateTransaction,
            ProposeCreateTransactionInput,
        )
        set_closed_period(ir, date(2025, 12, 31))
        ir.commit()
        ctx = ToolContext(db=ir, user_id="u", username="t", user_message="record it on 2025-06-15")
        tool = ProposeCreateTransaction()
        payload = ProposeCreateTransactionInput(
            date="2025-06-15", description="x", currency="IRR",
            lines=[
                {"account_code": "6112", "debit": 100, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 100},
            ],
        )
        with pytest.raises(ToolError) as ei:
            asyncio.run(tool.run(ctx, payload))
        assert ei.value.code == "period_locked"


# ---------------------------------------------------------------------------
# 5. Void / chargeback
# ---------------------------------------------------------------------------


def _issue(db, *, kind="sales", amount=1000, currency="GBP"):
    today = date.today()
    return create_invoice(InvoiceCreate(
        number=f"INV-{uuid.uuid4().hex[:6]}", kind=kind, issue_date=today,
        due_date=today, amount=amount, currency=currency, status="issued",
    ), db)


class TestVoidAndChargeback:
    def test_void_reverses_recognition_and_payments(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        add_payment(inv.id, PaymentCreate(amount=400), uk)
        assert _bal(uk, "1100") == 600  # AR open before void
        out = void_invoice(inv.id, uk)
        assert out.status == "voided"
        assert out.balance_due == 0
        assert _bal(uk, "1100") == 0    # recognition + payment reversed → AR flat
        assert _bal(uk, "4000") == 0    # revenue reversed
        assert _bal(uk, "1200") == 0    # cash reversed
        assert _balanced(uk)
        # Audit trail intact: invoice row still present, not hard-deleted.
        assert uk.get(Invoice, inv.id) is not None

    def test_chargeback_reopens_balance(self, uk):
        inv = _issue(uk, kind="sales", amount=1000)
        add_payment(inv.id, PaymentCreate(amount=1000), uk)
        pay = uk.execute(select(Payment).where(Payment.invoice_id == inv.id)).scalars().one()
        out = reverse_payment(inv.id, pay.id, uk)
        assert out.balance_due == 1000          # fully reopened
        assert out.status in ("issued", "partially_paid")
        assert _bal(uk, "1100") == 1000         # AR back to full
        assert _balanced(uk)

    def test_voided_excluded_from_ar_aging(self, uk):
        from app.api.manager_reports import accounts_receivable
        inv = _issue(uk, kind="sales", amount=1000)
        void_invoice(inv.id, uk)
        data = accounts_receivable(None, None, uk)
        assert all(i["invoice_id"] != str(inv.id) for i in data["items"])


# ---------------------------------------------------------------------------
# 6. Fiscal boundary
# ---------------------------------------------------------------------------


class TestFiscalBoundary:
    def test_dec31_vs_jan1_land_in_correct_year(self, ir):
        from app.services.reporting.repository import trial_balance_rows

        def post(d):
            t = Transaction(date=d, description="boundary", currency="IRR")
            ir.add(t)
            ir.flush()
            rev = ir.execute(select(Account).where(Account.code == "4110")).scalars().one()
            cash = ir.execute(select(Account).where(Account.code == "1110")).scalars().one()
            ir.add(TransactionLine(transaction_id=t.id, account_id=cash.id, debit=100, credit=0))
            ir.add(TransactionLine(transaction_id=t.id, account_id=rev.id, debit=0, credit=100))
            ir.commit()

        post(date(2025, 12, 31))
        post(date(2026, 1, 1))
        rows_2025 = trial_balance_rows(ir, date(2025, 1, 1), date(2025, 12, 31))
        rev_2025 = sum(c for code, _n, _d, c in rows_2025 if code == "4110")
        rows_2026 = trial_balance_rows(ir, date(2026, 1, 1), date(2026, 12, 31))
        rev_2026 = sum(c for code, _n, _d, c in rows_2026 if code == "4110")
        assert rev_2025 == 100  # Dec-31 lands in 2025
        assert rev_2026 == 100  # Jan-1 lands in 2026
