"""Expenses: mileage claims and the approval workflow.

A mileage claim's amount = round(distance · rate). Below the company approval
threshold it posts immediately (DR mileage_expense / CR expenses_payable) and
is "approved"; above it the claim is routed (status "pending_approval") and
posts only when an approver approves it. Reimbursing the employee
(DR expenses_payable / CR bank) is a separate, explicitly confirmed step.
Every write is balanced, period-locked and audited.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.account import Account
from app.models.entity import Entity
from app.models.mileage_claim import MileageClaim
from app.models.transaction import Transaction, TransactionLine
from app.services.account_resolver import resolve_account_code
from app.services.audit_service import log_audit_event
from app.core.request_context import get_current_actor


def _decider(approver: str | None) -> str:
    """Who acted on the claim: an explicit approver, else the current user."""
    if approver:
        return approver
    actor = get_current_actor()
    return getattr(actor, "username", None) or "admin"
from app.services.expense_settings import (
    get_approval_threshold,
    get_expense_settings,
    set_expense_settings,
)
from app.services.fx_service import get_reporting_currency
from app.services.period_service import assert_period_open

router = APIRouter(prefix="/expenses", tags=["expenses"])

_date = date


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExpenseSettingsPayload(BaseModel):
    mileage_rate: float | None = Field(None, ge=0)
    mileage_unit: str | None = None
    approval_threshold: int | None = Field(None, ge=0)


class MileageCreate(BaseModel):
    entity_id: UUID | None = None
    employee_name: str | None = None
    claim_date: _date
    distance: float = Field(..., gt=0)
    purpose: str | None = None
    # Optional overrides; default to the company mileage settings.
    rate: float | None = Field(None, ge=0)
    unit: str | None = None
    currency: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round(x: float) -> int:
    return int(x + 0.5)


def _claim_read(c: MileageClaim) -> dict:
    return {
        "id": str(c.id),
        "entity_id": str(c.entity_id) if c.entity_id else None,
        "employee_name": c.employee_name,
        "claim_date": c.claim_date.isoformat(),
        "distance": float(c.distance or 0),
        "unit": c.unit,
        "rate": float(c.rate or 0),
        "amount": int(c.amount or 0),
        "currency": c.currency,
        "purpose": c.purpose,
        "status": c.status,
        "transaction_id": str(c.transaction_id) if c.transaction_id else None,
        "reimbursement_transaction_id": (
            str(c.reimbursement_transaction_id) if c.reimbursement_transaction_id else None
        ),
        "decided_by": c.decided_by,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
    }


def _post(db: Session, *, on: date, reference: str, description: str, currency: str,
          lines: list[tuple[str, int, int, str]]) -> Transaction:
    assert_period_open(db, on)
    total_dr = sum(d for _, d, _c, _l in lines)
    total_cr = sum(c for _, _d, c, _l in lines)
    if total_dr != total_cr or total_dr <= 0:
        raise HTTPException(status_code=400, detail="Expense entry must be balanced and non-zero.")
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


def _post_claim_accrual(db: Session, claim: MileageClaim) -> None:
    """DR mileage_expense / CR expenses_payable — the reimbursable accrual."""
    txn = _post(
        db, on=claim.claim_date, reference=f"MILEAGE-{claim.claim_date.isoformat()}",
        description=f"Mileage claim — {claim.employee_name} ({claim.distance} {claim.unit})",
        currency=claim.currency,
        lines=[
            (resolve_account_code(db, "mileage_expense"), claim.amount, 0, "Mileage expense"),
            (resolve_account_code(db, "expenses_payable"), 0, claim.amount, "Employee expenses payable"),
        ],
    )
    claim.transaction_id = txn.id


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("/settings")
def read_settings(db: Session = Depends(get_db)) -> dict:
    return get_expense_settings(db)


@router.post("/settings")
def write_settings(payload: ExpenseSettingsPayload, db: Session = Depends(get_db)) -> dict:
    return set_expense_settings(
        db, mileage_rate=payload.mileage_rate, mileage_unit=payload.mileage_unit,
        approval_threshold=payload.approval_threshold,
    )


# ---------------------------------------------------------------------------
# Mileage claims
# ---------------------------------------------------------------------------


@router.post("/mileage", status_code=201)
def create_mileage(payload: MileageCreate, db: Session = Depends(get_db)) -> dict:
    """Create a mileage claim. amount = round(distance · rate). Below the
    approval threshold it posts immediately; above it, it's routed for approval."""
    settings = get_expense_settings(db)
    rate = float(payload.rate) if payload.rate is not None else float(settings["mileage_rate"])
    unit = (payload.unit or settings["mileage_unit"])
    cur = (payload.currency or get_reporting_currency(db) or "IRR").upper()
    if rate <= 0:
        raise HTTPException(status_code=422, detail="No mileage rate set — configure one in expense settings.")

    name = payload.employee_name
    if payload.entity_id is not None:
        ent = db.get(Entity, payload.entity_id)
        if not ent:
            raise HTTPException(status_code=404, detail="Employee entity not found.")
        if ent.type != "employee":
            raise HTTPException(status_code=422, detail="Mileage is reimbursed to an employee entity.")
        name = name or ent.name
    if not name:
        raise HTTPException(status_code=422, detail="An employee name (or entity) is required.")

    amount = _round(payload.distance * rate)
    threshold = int(settings["approval_threshold"])
    needs_approval = threshold > 0 and amount > threshold

    claim = MileageClaim(
        entity_id=payload.entity_id, employee_name=name, claim_date=payload.claim_date,
        distance=payload.distance, unit=unit, rate=rate, amount=amount, currency=cur,
        purpose=payload.purpose,
        status="pending_approval" if needs_approval else "approved",
    )
    db.add(claim)
    db.flush()
    if not needs_approval:
        _post_claim_accrual(db, claim)
    log_audit_event(db, action="create", entity_type="mileage_claim", entity_id=str(claim.id),
                    detail=f"Mileage claim {name} {amount} {cur}"
                           + (" — routed for approval" if needs_approval else " — posted"))
    db.commit()
    db.refresh(claim)
    return {**_claim_read(claim), "needs_approval": needs_approval}


def _expense_own_scope():
    """Self-service (Employee) callers see only their own claims."""
    from app.core.permissions import Perm, own_scope
    from app.core.request_context import get_current_actor
    return own_scope(get_current_actor(), Perm.BOOKS_READ)


@router.get("")
def list_expenses(status: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = select(MileageClaim).order_by(MileageClaim.created_at.desc())
    if status:
        q = q.where(MileageClaim.status == status.strip().lower())
    restricted, own = _expense_own_scope()
    if restricted:
        if not own:
            return []  # self-service caller with no linked employee entity
        q = q.where(MileageClaim.entity_id == UUID(str(own)))
    return [_claim_read(c) for c in db.execute(q).scalars().all()]


@router.get("/{claim_id}")
def get_expense(claim_id: UUID, db: Session = Depends(get_db)) -> dict:
    c = db.get(MileageClaim, claim_id)
    if not c:
        raise HTTPException(status_code=404, detail="Claim not found.")
    restricted, own = _expense_own_scope()
    if restricted and str(c.entity_id) != str(own or ""):
        raise HTTPException(status_code=404, detail="Claim not found.")
    return _claim_read(c)


@router.post("/{claim_id}/approve")
def approve_expense(claim_id: UUID, approver: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Approve a routed claim and post it (confirm-gated). Records the approver."""
    c = db.get(MileageClaim, claim_id)
    if not c:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if c.status != "pending_approval":
        raise HTTPException(status_code=409, detail=f"Claim is {c.status}; nothing to approve.")
    _post_claim_accrual(db, c)
    c.status = "approved"
    c.decided_by = _decider(approver)
    c.decided_at = datetime.now(timezone.utc)
    log_audit_event(db, action="approve", entity_type="mileage_claim", entity_id=str(c.id),
                    username=c.decided_by, detail=f"Approved mileage claim {c.amount} {c.currency}")
    db.commit()
    db.refresh(c)
    return _claim_read(c)


@router.post("/{claim_id}/reject")
def reject_expense(claim_id: UUID, approver: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Reject a routed claim — posts nothing. Records the decider."""
    c = db.get(MileageClaim, claim_id)
    if not c:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if c.status != "pending_approval":
        raise HTTPException(status_code=409, detail=f"Claim is {c.status}; nothing to reject.")
    c.status = "rejected"
    c.decided_by = _decider(approver)
    c.decided_at = datetime.now(timezone.utc)
    log_audit_event(db, action="reject", entity_type="mileage_claim", entity_id=str(c.id),
                    username=c.decided_by, detail=f"Rejected mileage claim {c.amount} {c.currency}")
    db.commit()
    db.refresh(c)
    return _claim_read(c)


@router.post("/{claim_id}/reimburse")
def reimburse_expense(claim_id: UUID, bank_account_code: str | None = None,
                      db: Session = Depends(get_db)) -> dict:
    """Pay the employee — DR expenses_payable / CR bank. Separate, explicit step;
    only an approved-and-posted claim can be reimbursed, and only once."""
    c = db.get(MileageClaim, claim_id)
    if not c:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if c.status != "approved" or not c.transaction_id:
        raise HTTPException(status_code=409, detail="Only an approved, posted claim can be reimbursed.")
    if c.reimbursement_transaction_id:
        raise HTTPException(status_code=409, detail="Claim already reimbursed.")

    bank = bank_account_code
    if not (bank and db.execute(select(Account.id).where(Account.code == bank)).first()):
        bank = resolve_account_code(db, "bank")
    txn = _post(
        db, on=c.claim_date, reference=f"MILEAGE-PAY-{c.claim_date.isoformat()}",
        description=f"Reimburse mileage — {c.employee_name}",
        currency=c.currency,
        lines=[
            (resolve_account_code(db, "expenses_payable"), c.amount, 0, "Clear employee payable"),
            (bank, 0, c.amount, "Mileage reimbursed from bank"),
        ],
    )
    c.reimbursement_transaction_id = txn.id
    c.status = "reimbursed"
    log_audit_event(db, action="reimburse", entity_type="mileage_claim", entity_id=str(c.id),
                    detail=f"Reimbursed mileage claim {c.amount} {c.currency}")
    db.commit()
    db.refresh(c)
    return _claim_read(c)
