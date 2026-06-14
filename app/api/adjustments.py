"""Period-end adjustments: accruals (with auto-reverse), prepayments with an
amortization schedule, and straight-line depreciation. Every entry is a
balanced double-entry posting, audited, and blocked from closed periods.
Reversals reuse the shared ledger reversal machinery.
"""
from __future__ import annotations

import uuid
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.account import Account
from app.models.adjustment import Adjustment
from app.models.transaction import Transaction, TransactionLine
from app.services.account_resolver import resolve_account_code
from app.services.audit_service import log_audit_event
from app.services.period_service import assert_period_open
from app.services.reporting.ledger_service import LedgerService

router = APIRouter(prefix="/adjustments", tags=["adjustments"])

_date = date  # alias for schema fields named `date`


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    return date(y, m % 12 + 1, min(d.day, 28))  # clamp day to keep it valid


def _post(db: Session, *, on: date, reference: str, description: str, currency: str,
          lines: list[tuple[str, int, int, str]]) -> Transaction:
    """Post a balanced entry (lines = [(code, debit, credit, desc)]) with an
    audit row, after checking the period is open and accounts resolve."""
    assert_period_open(db, on)
    total_dr = sum(d for _, d, _c, _ld in lines)
    total_cr = sum(c for _, _d, c, _ld in lines)
    if total_dr != total_cr or total_dr <= 0:
        raise HTTPException(status_code=400, detail="Adjustment entry must be balanced and non-zero.")
    txn = Transaction(date=on, reference=reference[:128], description=description,
                      currency=(currency or "IRR").strip().upper())
    db.add(txn)
    db.flush()
    for code, debit, credit, line_desc in lines:
        acc = db.execute(select(Account).where(Account.code == code)).scalars().one_or_none()
        if not acc:
            raise HTTPException(status_code=422, detail=f"Account not found: {code}")
        db.add(TransactionLine(transaction_id=txn.id, account_id=acc.id,
                               debit=int(debit), credit=int(credit), line_description=line_desc))
    db.flush()
    log_audit_event(db, action="create", entity_type="transaction", entity_id=str(txn.id),
                    detail=description)
    return txn


def _period_amount(adj: Adjustment, index: int) -> int:
    """Amount released in period ``index`` (0-based). Even split with the
    remainder on the final period so the total reconciles exactly."""
    total = int(adj.amount or 0)
    if adj.kind == "depreciation":
        total = int(adj.amount or 0) - int(adj.residual or 0)
    periods = max(1, int(adj.periods or 1))
    base = total // periods
    if index >= periods - 1:
        return total - base * (periods - 1)
    return base


def _released_to_date(adj: Adjustment) -> int:
    return sum(_period_amount(adj, i) for i in range(int(adj.periods_posted or 0)))


def _to_read(adj: Adjustment) -> dict:
    total = int(adj.amount or 0)
    depreciable = total - int(adj.residual or 0) if adj.kind == "depreciation" else total
    released = _released_to_date(adj)
    return {
        "id": str(adj.id),
        "kind": adj.kind,
        "description": adj.description,
        "currency": adj.currency,
        "amount": total,
        "residual": int(adj.residual or 0),
        "periods": int(adj.periods or 1),
        "period_months": int(adj.period_months or 1),
        "start_date": adj.start_date.isoformat() if adj.start_date else None,
        "direction": adj.direction,
        "auto_reverse": adj.auto_reverse,
        "periods_posted": int(adj.periods_posted or 0),
        "status": adj.status,
        "per_period": _period_amount(adj, 0) if adj.kind in ("prepayment", "depreciation") else None,
        "released_to_date": released if adj.kind in ("prepayment", "depreciation") else None,
        "remaining": (depreciable - released) if adj.kind in ("prepayment", "depreciation") else None,
        "net_book_value": (total - released) if adj.kind == "depreciation" else None,
        "transaction_id": str(adj.transaction_id) if adj.transaction_id else None,
        "reversal_transaction_id": str(adj.reversal_transaction_id) if adj.reversal_transaction_id else None,
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AccrualCreate(BaseModel):
    amount: int = Field(..., gt=0)
    date: _date
    description: str | None = None
    currency: str = "IRR"
    direction: str = Field("expense", description="expense (DR expense/CR accrued liability) or income (DR accrued income/CR revenue)")
    auto_reverse: bool = True
    period_months: int = Field(1, ge=1)


class PrepaymentCreate(BaseModel):
    amount: int = Field(..., gt=0, description="Total prepaid amount to amortize")
    periods: int = Field(..., ge=1)
    start_date: _date
    period_months: int = Field(1, ge=1)
    description: str | None = None
    currency: str = "IRR"
    bank_account_code: str | None = None


class DepreciationCreate(BaseModel):
    cost: int = Field(..., gt=0)
    periods: int = Field(..., ge=1, description="Useful life in periods")
    start_date: _date
    residual: int = Field(0, ge=0)
    period_months: int = Field(1, ge=1)
    description: str | None = None
    currency: str = "IRR"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/accrual", status_code=201)
def create_accrual(payload: AccrualCreate, db: Session = Depends(get_db)) -> dict:
    """Record an accrual; optionally auto-reverse on the first day of the next
    period so it nets to zero across the two periods."""
    amount = int(payload.amount)
    cur = (payload.currency or "IRR").upper()
    desc = payload.description or "Accrual"
    ref = f"ACCR-{payload.date.isoformat()}"
    if payload.direction == "income":
        lines = [
            (resolve_account_code(db, "accrued_income"), amount, 0, "Accrued income"),
            (resolve_account_code(db, "revenue"), 0, amount, desc),
        ]
    else:
        lines = [
            (resolve_account_code(db, "expense"), amount, 0, desc),
            (resolve_account_code(db, "accrued_liability"), 0, amount, "Accrued liability"),
        ]
    txn = _post(db, on=payload.date, reference=ref, description=desc, currency=cur, lines=lines)

    adj = Adjustment(
        kind="accrual", description=desc, currency=cur, amount=amount,
        periods=1, period_months=payload.period_months, start_date=payload.date,
        direction=payload.direction, auto_reverse=payload.auto_reverse,
        periods_posted=1, status="complete", transaction_id=txn.id,
    )
    db.add(adj)
    db.flush()

    if payload.auto_reverse:
        # Reverse on the first day of the next period.
        reverse_on = _add_months(payload.date.replace(day=1), payload.period_months)
        assert_period_open(db, reverse_on)
        rev = LedgerService(db).reverse_journal_entry(
            transaction_id=txn.id, reverse_date=reverse_on,
            reference=f"REV-{ref}", description=f"Reversal of accrual {desc}",
        )
        adj.reversal_transaction_id = uuid.UUID(str(rev.transaction_id))

    db.commit()
    db.refresh(adj)
    return _to_read(adj)


@router.post("/prepayment", status_code=201)
def create_prepayment(payload: PrepaymentCreate, db: Session = Depends(get_db)) -> dict:
    """Record a prepayment (DR prepaid asset / CR bank) and set up an even
    amortization schedule released via /release."""
    amount = int(payload.amount)
    cur = (payload.currency or "IRR").upper()
    desc = payload.description or "Prepayment"
    bank = payload.bank_account_code
    if not (bank and db.execute(select(Account.id).where(Account.code == bank)).first()):
        bank = resolve_account_code(db, "bank")
    txn = _post(
        db, on=payload.start_date, reference=f"PREPAY-{payload.start_date.isoformat()}",
        description=desc, currency=cur,
        lines=[
            (resolve_account_code(db, "prepaid_expense"), amount, 0, desc),
            (bank, 0, amount, "Prepayment paid"),
        ],
    )
    adj = Adjustment(
        kind="prepayment", description=desc, currency=cur, amount=amount,
        periods=payload.periods, period_months=payload.period_months,
        start_date=payload.start_date, periods_posted=0, status="active",
        transaction_id=txn.id,
    )
    db.add(adj)
    db.commit()
    db.refresh(adj)
    return _to_read(adj)


@router.post("/depreciation", status_code=201)
def create_depreciation(payload: DepreciationCreate, db: Session = Depends(get_db)) -> dict:
    """Set up a straight-line depreciation schedule (no cash entry); releases
    post DR depreciation-expense / CR accumulated-depreciation each period."""
    if payload.residual >= payload.cost:
        raise HTTPException(status_code=400, detail="Residual must be less than cost.")
    adj = Adjustment(
        kind="depreciation", description=(payload.description or "Depreciation"),
        currency=(payload.currency or "IRR").upper(), amount=int(payload.cost),
        residual=int(payload.residual), periods=payload.periods,
        period_months=payload.period_months, start_date=payload.start_date,
        periods_posted=0, status="active",
    )
    db.add(adj)
    db.commit()
    db.refresh(adj)
    return _to_read(adj)


@router.post("/{adjustment_id}/release", status_code=201)
def release_period(adjustment_id: UUID, db: Session = Depends(get_db)) -> dict:
    """Post the next period of a prepayment amortization or depreciation
    schedule."""
    adj = db.get(Adjustment, adjustment_id)
    if not adj:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if adj.kind not in ("prepayment", "depreciation"):
        raise HTTPException(status_code=400, detail="Only prepayment/depreciation schedules can be released.")
    if adj.periods_posted >= adj.periods:
        raise HTTPException(status_code=400, detail="Schedule already fully released.")

    idx = adj.periods_posted
    amount = _period_amount(adj, idx)
    on = _add_months(adj.start_date, idx * adj.period_months)
    if adj.kind == "prepayment":
        lines = [
            (resolve_account_code(db, "expense"), amount, 0, f"Prepayment release {idx + 1}/{adj.periods}"),
            (resolve_account_code(db, "prepaid_expense"), 0, amount, "Release prepaid asset"),
        ]
        ref = f"PREPAY-REL-{on.isoformat()}"
    else:
        lines = [
            (resolve_account_code(db, "depreciation_expense"), amount, 0, f"Depreciation {idx + 1}/{adj.periods}"),
            (resolve_account_code(db, "accumulated_depreciation"), 0, amount, "Accumulated depreciation"),
        ]
        ref = f"DEPR-{on.isoformat()}"
    _post(db, on=on, reference=ref, description=(adj.description or adj.kind), currency=adj.currency, lines=lines)
    adj.periods_posted += 1
    if adj.periods_posted >= adj.periods:
        adj.status = "complete"
    db.commit()
    db.refresh(adj)
    return _to_read(adj)


@router.get("")
def list_adjustments(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Adjustment).order_by(Adjustment.created_at.desc())).scalars().all()
    return [_to_read(a) for a in rows]


@router.get("/{adjustment_id}")
def get_adjustment(adjustment_id: UUID, db: Session = Depends(get_db)) -> dict:
    adj = db.get(Adjustment, adjustment_id)
    if not adj:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    return _to_read(adj)
