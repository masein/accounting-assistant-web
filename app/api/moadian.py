"""سامانه مودیان export (roadmap §3.1, phase 1): readiness per invoice,
JSON export for a trusted provider (شرکت معتمد), result tracking, settings.
Direct submission with the company's signing key is phase 2."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.services.audit_service import log_audit_event
from app.services.moadian import builder
from app.services.moadian.settings import MoadianSettingsError, get_settings, save_settings

router = APIRouter(prefix="/moadian", tags=["moadian"])

STATES = ("pending", "exported", "confirmed", "rejected", "all")


class SettingsUpdate(BaseModel):
    memory_id: str | None = Field(None, max_length=12)
    default_sstid: str | None = Field(None, max_length=20)
    default_mu: str | None = Field(None, max_length=12)
    deadline_days: int | None = None


class ExportRequest(BaseModel):
    invoice_ids: list[UUID] = Field(..., min_length=1, max_length=500)


class ResultUpdate(BaseModel):
    status: str = Field(..., description="confirmed | rejected | pending (not sent)")
    reference: str | None = Field(None, max_length=64, description="The provider's / tax organisation's reference number")
    error: str | None = Field(None, max_length=2000, description="Why it was rejected")


def _load(db: Session, invoice_id: UUID) -> Invoice:
    inv = db.execute(select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.items))).scalars().one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return inv


def _row(db: Session, inv: Invoice, today: date) -> dict:
    b = builder.build(db, inv)
    party = db.get(Entity, inv.entity_id) if inv.entity_id else None
    due = builder.deadline(db, inv)
    return {
        "id": str(inv.id), "number": inv.number, "issue_date": inv.issue_date.isoformat(),
        "customer": party.name if party else None, "amount": int(inv.amount or 0), "currency": inv.currency,
        "status": inv.status, "moadian_status": inv.moadian_status, "taxid": inv.moadian_taxid,
        "reference": inv.moadian_reference, "error": inv.moadian_error,
        "exported_at": inv.moadian_exported_at.isoformat() if inv.moadian_exported_at else None,
        "deadline": due.isoformat(), "days_left": (due - today).days,
        "ready": b.ready, "problems": b.problems, "warnings": b.warnings,
    }


@router.get("/settings")
def read_settings(db: Session = Depends(get_db)) -> dict:
    return get_settings(db)


@router.put("/settings")
def write_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        saved = save_settings(db, **payload.model_dump(exclude_unset=True))
    except (MoadianSettingsError, ValueError) as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_audit_event(db, action="update", entity_type="moadian_settings", entity_id="moadian",
                    detail=f"مودیان settings: memory id {'set' if saved['memory_id'] else 'empty'}, "
                           f"deadline {saved['deadline_days']} days")
    db.commit()
    return saved


@router.get("/invoices")
def list_invoices(
    state: str = Query("pending", description="pending | exported | confirmed | rejected | all"),
    date_from: date | None = None, date_to: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if state not in STATES:
        raise HTTPException(status_code=422, detail=f"state must be one of {', '.join(STATES)}")
    q = select(Invoice).where(Invoice.kind == "sales", Invoice.status.in_(builder.SENDABLE)).options(
        selectinload(Invoice.items)).order_by(Invoice.issue_date, Invoice.number)
    if state == "pending":
        q = q.where(Invoice.moadian_status.is_(None))
    elif state != "all":
        q = q.where(Invoice.moadian_status == state)
    if date_from:
        q = q.where(Invoice.issue_date >= date_from)
    if date_to:
        q = q.where(Invoice.issue_date <= date_to)
    today = date.today()
    rows = [_row(db, inv, today) for inv in db.execute(q).scalars().all()]
    return {"settings": get_settings(db), "invoices": rows,
            "overdue": sum(1 for r in rows if r["moadian_status"] is None and r["days_left"] < 0)}


@router.get("/invoices/{invoice_id}/preview")
def preview(invoice_id: UUID, db: Session = Depends(get_db)) -> dict:
    """The packet as it would be exported. An invoice that was exported
    before shows its real tax number; otherwise the number is provisional."""
    inv = _load(db, invoice_id)
    serial = inv.moadian_serial or builder.next_serial(db)
    b = builder.build(db, inv, serial=serial)
    return {"packet": {k: v for k, v in (b.packet or {}).items() if k != "_meta"},
            "provisional": inv.moadian_serial is None, "ready": b.ready,
            "problems": b.problems, "warnings": b.warnings}


@router.post("/export")
def export(payload: ExportRequest, db: Session = Depends(get_db)) -> dict:
    """Build the packets for the chosen invoices. Each exported invoice gets
    its serial (once, for good) and 22-char tax number and is marked
    ``exported``. Invoices with problems, or already confirmed, are skipped
    and listed with the reasons."""
    packets, index, skipped = [], [], []
    now = datetime.now(timezone.utc)
    for iid in dict.fromkeys(payload.invoice_ids):
        inv = db.execute(select(Invoice).where(Invoice.id == iid).options(selectinload(Invoice.items))).scalars().one_or_none()
        if inv is None:
            skipped.append({"id": str(iid), "number": None, "problems": ["Invoice not found."]})
            continue
        if inv.moadian_status == "confirmed":
            skipped.append({"id": str(inv.id), "number": inv.number,
                            "problems": ["Already confirmed by سامانه مودیان; send a corrective invoice instead."]})
            continue
        check = builder.build(db, inv)
        if not check.ready:
            skipped.append({"id": str(inv.id), "number": inv.number, "problems": check.problems})
            continue
        if inv.moadian_serial is None:
            inv.moadian_serial = builder.next_serial(db)
            db.flush()
        b = builder.build(db, inv, serial=inv.moadian_serial)
        inv.moadian_taxid = b.packet["header"]["taxid"]
        inv.moadian_status = "exported"
        inv.moadian_exported_at = now
        inv.moadian_error = None
        packets.append({k: v for k, v in b.packet.items() if k != "_meta"})
        index.append({"invoice_id": str(inv.id), "number": inv.number, "taxid": inv.moadian_taxid,
                      "serial": inv.moadian_serial, "warnings": b.warnings})
    if index:
        log_audit_event(db, action="export", entity_type="moadian", entity_id=now.strftime("%Y%m%d%H%M%S"),
                        detail=f"{len(index)} invoice(s) exported for سامانه مودیان: "
                               + ", ".join(x["number"] for x in index)[:900])
    db.commit()
    return {
        "file_name": f"moadian-{now.strftime('%Y%m%d-%H%M%S')}.json",
        "generated_at": now.isoformat(), "count": len(packets),
        "packets": packets, "invoices": index, "skipped": skipped,
    }


@router.patch("/invoices/{invoice_id}")
def record_result(invoice_id: UUID, payload: ResultUpdate, db: Session = Depends(get_db)) -> dict:
    """Record what the provider / tax organisation answered."""
    inv = _load(db, invoice_id)
    status = payload.status.strip().lower()
    if status not in ("confirmed", "rejected", "pending"):
        raise HTTPException(status_code=422, detail="status must be confirmed, rejected or pending.")
    if status in ("confirmed", "rejected") and inv.moadian_taxid is None:
        raise HTTPException(status_code=409, detail="Export the invoice first; it has no tax number yet.")
    inv.moadian_status = None if status == "pending" else status
    if payload.reference is not None:
        inv.moadian_reference = payload.reference.strip() or None
    inv.moadian_error = (payload.error or "").strip() or None if status == "rejected" else None
    log_audit_event(db, action="update", entity_type="invoice", entity_id=str(inv.id),
                    detail=f"مودیان status of {inv.number}: {status}"
                           + (f" (ref {inv.moadian_reference})" if inv.moadian_reference else ""))
    db.commit()
    return _row(db, _load(db, invoice_id), date.today())
