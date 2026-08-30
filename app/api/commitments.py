"""Installments (اقساط) and cheques (چک): what's due, and settling it."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.commitment import CHEQUE, INSTALLMENT, PAY, PENDING, RECEIVE, Commitment
from app.services import commitment_service as svc

router = APIRouter(prefix="/commitments", tags=["commitments"])


class InstallmentPlanCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    total_amount: int = Field(..., gt=0)
    count: int = Field(..., ge=1, le=600)
    first_due: date
    direction: str = Field(default=PAY, pattern="^(pay|receive)$")
    counterparty: str | None = Field(default=None, max_length=256)
    counter_account_code: str | None = Field(default=None, max_length=64)
    note: str | None = None


class ChequeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=256)
    amount: int = Field(..., gt=0)
    due_date: date
    direction: str = Field(default=PAY, pattern="^(pay|receive)$")
    reference: str | None = Field(default=None, max_length=64)
    bank_name: str | None = Field(default=None, max_length=128)
    counterparty: str | None = Field(default=None, max_length=256)
    counter_account_code: str | None = Field(default=None, max_length=64)
    note: str | None = None


class SettleRequest(BaseModel):
    on: date | None = None
    post: bool = True


class CommitmentRead(BaseModel):
    id: UUID
    kind: str
    direction: str
    title: str
    amount: int
    due_date: date
    status: str
    plan_id: UUID | None = None
    sequence: int | None = None
    plan_total: int | None = None
    reference: str | None = None
    bank_name: str | None = None
    counterparty: str | None = None
    counter_account_code: str | None = None
    settled_on: date | None = None
    settled_transaction_id: UUID | None = None

    model_config = {"from_attributes": True}


class CommitmentSummary(BaseModel):
    payable: int
    receivable: int
    count: int
    next_due_date: date | None = None


@router.get("", response_model=list[CommitmentRead])
def list_commitments(
    db: Session = Depends(get_db),
    status: str | None = Query(None),
    kind: str | None = Query(None, pattern="^(installment|cheque)$"),
    direction: str | None = Query(None, pattern="^(pay|receive)$"),
) -> list[CommitmentRead]:
    q = select(Commitment)
    if status:
        q = q.where(Commitment.status == status)
    if kind:
        q = q.where(Commitment.kind == kind)
    if direction:
        q = q.where(Commitment.direction == direction)
    rows = db.execute(q.order_by(Commitment.due_date)).scalars().all()
    return [CommitmentRead.model_validate(r) for r in rows]


@router.get("/summary", response_model=CommitmentSummary)
def summary(db: Session = Depends(get_db)) -> CommitmentSummary:
    return CommitmentSummary(**svc.totals(db))


@router.get("/plans/{plan_id}")
def plan(plan_id: UUID, db: Session = Depends(get_db)) -> dict:
    data = svc.plan_summary(db, plan_id)
    if not data:
        raise HTTPException(status_code=404, detail="Plan not found")
    return data


@router.post("/installments", response_model=list[CommitmentRead], status_code=201)
def create_plan(payload: InstallmentPlanCreate, db: Session = Depends(get_db)) -> list[CommitmentRead]:
    rows = svc.create_installment_plan(
        db, title=payload.title, total_amount=payload.total_amount, count=payload.count,
        first_due=payload.first_due, direction=payload.direction,
        counterparty=payload.counterparty, counter_account_code=payload.counter_account_code,
        note=payload.note,
    )
    db.commit()
    return [CommitmentRead.model_validate(r) for r in rows]


@router.post("/cheques", response_model=CommitmentRead, status_code=201)
def create_cheque(payload: ChequeCreate, db: Session = Depends(get_db)) -> CommitmentRead:
    row = svc.create_cheque(
        db, title=payload.title, amount=payload.amount, due_date=payload.due_date,
        direction=payload.direction, reference=payload.reference, bank_name=payload.bank_name,
        counterparty=payload.counterparty, counter_account_code=payload.counter_account_code,
        note=payload.note,
    )
    db.commit()
    return CommitmentRead.model_validate(row)


def _get(db: Session, commitment_id: UUID) -> Commitment:
    row = db.get(Commitment, commitment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Commitment not found")
    return row


@router.post("/{commitment_id}/settle", response_model=CommitmentRead)
def settle(commitment_id: UUID, payload: SettleRequest, db: Session = Depends(get_db)) -> CommitmentRead:
    row = svc.settle(db, _get(db, commitment_id), on=payload.on, post=payload.post)
    db.commit()
    return CommitmentRead.model_validate(row)


@router.post("/{commitment_id}/bounce", response_model=CommitmentRead)
def bounce(commitment_id: UUID, db: Session = Depends(get_db)) -> CommitmentRead:
    row = svc.mark_bounced(db, _get(db, commitment_id))
    db.commit()
    return CommitmentRead.model_validate(row)


@router.delete("/{commitment_id}", status_code=204)
def delete_commitment(commitment_id: UUID, db: Session = Depends(get_db)) -> None:
    row = _get(db, commitment_id)
    if row.status != PENDING:
        raise HTTPException(status_code=400, detail="Only a pending commitment can be deleted")
    db.delete(row)
    db.commit()
