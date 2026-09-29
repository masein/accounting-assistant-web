"""Installments (اقساط) and cheques (چک): what's due, and settling it — and a
cheque's whole life: deposit, clear, bounce, deposit again, return, pass on,
Sayad registration (roadmap 2026-09 §3.4, ``app/services/cheques.py``)."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.http_headers import content_disposition
from app.db.session import get_db
from app.models.commitment import NOTES, OPEN_STATUSES, PAY, PENDING, Commitment
from app.services import cheque_print, cheques
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
    sayad_id: str | None = Field(default=None, max_length=32, description="The 16-digit Sayad id on the cheque")
    sayad_registered_on: date | None = None
    invoice_id: UUID | None = Field(default=None, description="The invoice (received) or bill (issued) it pays")
    entity_id: UUID | None = None
    on: date | None = Field(default=None, description="Day received / issued (defaults to today)")


class SettleRequest(BaseModel):
    on: date | None = None
    post: bool = True
    bank_account_code: str | None = Field(default=None, max_length=64)


class StepRequest(BaseModel):
    on: date | None = None
    note: str | None = Field(default=None, max_length=500)


class DepositRequest(BaseModel):
    on: date | None = None
    bank_account_code: str | None = Field(default=None, max_length=64)


class EndorseRequest(BaseModel):
    to: str = Field(default="", max_length=256, description="Who it was passed to")
    on: date | None = None
    account_code: str | None = Field(default=None, max_length=64, description="Their account (e.g. the supplier)")
    invoice_id: UUID | None = Field(default=None, description="A bill it pays")


class SayadRequest(BaseModel):
    sayad_id: str | None = Field(default=None, max_length=32)
    on: date | None = None


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
    sayad_id: str | None = None
    sayad_registered_on: date | None = None
    ledger_mode: str = "direct"
    deposited_on: date | None = None
    deposit_account_code: str | None = None
    invoice_id: UUID | None = None
    invoice_number: str | None = None
    endorsed_to: str | None = None
    needs_sayad: bool = False

    model_config = {"from_attributes": True}


class CommitmentSummary(BaseModel):
    payable: int
    receivable: int
    count: int
    next_due_date: date | None = None
    cheques_in_hand: int = 0
    cheques_at_bank: int = 0
    cheques_bounced: int = 0


@router.get("", response_model=list[CommitmentRead])
def list_commitments(
    db: Session = Depends(get_db),
    status: str | None = Query(None, description="A status, or 'open' for everything still owed or awaited"),
    kind: str | None = Query(None, pattern="^(installment|cheque)$"),
    direction: str | None = Query(None, pattern="^(pay|receive)$"),
) -> list[CommitmentRead]:
    q = select(Commitment)
    if status == "open":
        q = q.where(Commitment.status.in_(OPEN_STATUSES))
    elif status:
        q = q.where(Commitment.status == status)
    if kind:
        q = q.where(Commitment.kind == kind)
    if direction:
        q = q.where(Commitment.direction == direction)
    rows = db.execute(q.order_by(Commitment.due_date)).scalars().all()
    return _read_many(db, rows)


def _read_many(db: Session, rows) -> list[CommitmentRead]:
    from app.models.invoice import Invoice
    ids = {r.invoice_id for r in rows if r.invoice_id}
    numbers = dict(db.execute(select(Invoice.id, Invoice.number).where(Invoice.id.in_(ids))).all()) if ids else {}
    out = []
    for r in rows:
        item = CommitmentRead.model_validate(r)
        item.invoice_number = numbers.get(r.invoice_id)
        item.needs_sayad = cheques.needs_sayad(db, r)
        out.append(item)
    return out


def _read(db: Session, row: Commitment) -> CommitmentRead:
    return _read_many(db, [row])[0]


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
    return _read_many(db, rows)


@router.post("/cheques", response_model=CommitmentRead, status_code=201)
def create_cheque(payload: ChequeCreate, db: Session = Depends(get_db)) -> CommitmentRead:
    row = svc.create_cheque(
        db, title=payload.title, amount=payload.amount, due_date=payload.due_date,
        direction=payload.direction, reference=payload.reference, bank_name=payload.bank_name,
        counterparty=payload.counterparty, counter_account_code=payload.counter_account_code,
        note=payload.note, sayad_id=payload.sayad_id, sayad_registered_on=payload.sayad_registered_on,
        invoice_id=payload.invoice_id, entity_id=payload.entity_id, on=payload.on,
    )
    db.commit()
    return _read(db, row)


# --- printing an issued cheque (§3.4 part 2) — before the /{commitment_id} routes ---------------------------

class PrintLayoutUpdate(BaseModel):
    width: float | None = None
    height: float | None = None
    offset_x: float | None = None
    offset_y: float | None = None
    font_size: float | None = None
    fields: dict[str, dict[str, float]] = Field(default_factory=dict)


class PrintRequest(BaseModel):
    payee: str | None = Field(default=None, max_length=256, description="در وجه — defaults to the cheque's party")
    national_id: str | None = Field(default=None, max_length=32, description="The payee's national id / کد ملی")
    on: date | None = Field(default=None, description="The date written on the cheque (defaults to its due date)")
    guide: bool = Field(default=False, description="Outline and field names, for calibrating on plain paper")


def _pdf(data: bytes, name: str) -> Response:
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(name, inline=True), "Cache-Control": "no-store"})


@router.get("/print-layout")
def read_print_layout(db: Session = Depends(get_db)) -> dict:
    return cheque_print.get_layout(db)


@router.put("/print-layout")
def save_print_layout(payload: PrintLayoutUpdate, db: Session = Depends(get_db)) -> dict:
    out = cheque_print.save_layout(db, payload.model_dump(exclude_none=True))
    db.commit()
    return out


@router.post("/print-layout/reset")
def reset_print_layout(db: Session = Depends(get_db)) -> dict:
    out = cheque_print.reset_layout(db)
    db.commit()
    return out


@router.post("/print-test")
def print_test(db: Session = Depends(get_db)) -> Response:
    """The guide with sample values, to calibrate before there is a cheque to print."""
    from types import SimpleNamespace
    sample = SimpleNamespace(amount=12_500_000, due_date=date.today(), counterparty=cheque_print.SAMPLE["payee"],
                             entity_id=None)
    values = cheque_print.cheque_values(db, sample, national_id=cheque_print.SAMPLE["national_id"])
    return _pdf(cheque_print.render_pdf(db, values, guide=True), "cheque-guide.pdf")


def _get(db: Session, commitment_id: UUID) -> Commitment:
    row = db.get(Commitment, commitment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Commitment not found")
    return row


@router.post("/{commitment_id}/settle", response_model=CommitmentRead)
def settle(commitment_id: UUID, payload: SettleRequest, db: Session = Depends(get_db)) -> CommitmentRead:
    row = _get(db, commitment_id)
    if row.kind == "cheque":
        row = cheques.clear(db, row, on=payload.on, post=payload.post, bank_code=payload.bank_account_code)
    else:
        row = svc.settle(db, row, on=payload.on, post=payload.post)
    db.commit()
    return _read(db, row)


@router.post("/{commitment_id}/bounce", response_model=CommitmentRead)
def bounce(commitment_id: UUID, payload: StepRequest | None = None, db: Session = Depends(get_db)) -> CommitmentRead:
    row = _get(db, commitment_id)
    if row.kind != "cheque":
        raise HTTPException(status_code=400, detail="Only a cheque can bounce")
    row = cheques.bounce(db, row, on=payload.on if payload else None, note=payload.note if payload else None)
    db.commit()
    return _read(db, row)


@router.post("/{commitment_id}/deposit", response_model=CommitmentRead)
def deposit(commitment_id: UUID, payload: DepositRequest, db: Session = Depends(get_db)) -> CommitmentRead:
    row = cheques.deposit(db, _get(db, commitment_id), on=payload.on, bank_code=payload.bank_account_code)
    db.commit()
    return _read(db, row)


@router.post("/{commitment_id}/return", response_model=CommitmentRead)
def return_cheque(commitment_id: UUID, payload: StepRequest, db: Session = Depends(get_db)) -> CommitmentRead:
    row = cheques.return_cheque(db, _get(db, commitment_id), on=payload.on, note=payload.note)
    db.commit()
    return _read(db, row)


@router.post("/{commitment_id}/endorse", response_model=CommitmentRead)
def endorse(commitment_id: UUID, payload: EndorseRequest, db: Session = Depends(get_db)) -> CommitmentRead:
    row = cheques.endorse(db, _get(db, commitment_id), to=payload.to, on=payload.on,
                          account_code=payload.account_code, invoice_id=payload.invoice_id)
    db.commit()
    return _read(db, row)


@router.post("/{commitment_id}/sayad", response_model=CommitmentRead)
def register_sayad(commitment_id: UUID, payload: SayadRequest, db: Session = Depends(get_db)) -> CommitmentRead:
    row = cheques.register_sayad(db, _get(db, commitment_id), sayad_id=payload.sayad_id, on=payload.on)
    db.commit()
    return _read(db, row)


@router.post("/{commitment_id}/print")
def print_cheque(commitment_id: UUID, payload: PrintRequest, db: Session = Depends(get_db)) -> Response:
    """The cheque's leaf as a PDF (issued cheques). A real print goes in its history."""
    row = _get(db, commitment_id)
    if row.kind != "cheque" or row.direction != PAY:
        raise HTTPException(status_code=400, detail="Only a cheque we issue is printed; a received one is written by its drawer.")
    if row.status in ("settled", "returned", "cancelled"):
        raise HTTPException(status_code=409, detail=f"This cheque is {row.status}.")
    values = cheque_print.cheque_values(db, row, payee=payload.payee, national_id=payload.national_id, on=payload.on)
    pdf = cheque_print.render_pdf(db, values, guide=payload.guide)
    if not payload.guide:
        cheques.record_print(db, row, payee=values.get("payee") or None)
        db.commit()
    return _pdf(pdf, f"cheque-{row.reference or str(row.id)[:8]}.pdf")


@router.get("/{commitment_id}/history")
def history(commitment_id: UUID, db: Session = Depends(get_db)) -> dict:
    row = _get(db, commitment_id)
    return {"id": str(row.id), "status": row.status, "events": cheques.history(db, row)}


@router.delete("/{commitment_id}", status_code=204)
def delete_commitment(commitment_id: UUID, db: Session = Depends(get_db)) -> None:
    row = _get(db, commitment_id)
    if row.status != PENDING:
        raise HTTPException(status_code=400, detail="Only a pending commitment can be deleted")
    if row.ledger_mode == NOTES and row.holding_account_code:
        raise HTTPException(status_code=409, detail=(
            "This cheque is in the books. Mark it returned instead — that reverses its entry."))
    db.delete(row)
    db.commit()
