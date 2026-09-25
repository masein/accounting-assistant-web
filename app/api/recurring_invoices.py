"""Recurring sales invoices (roadmap §4.2, part 3). The generator lives in
app/services/recurring_invoice_service.py; the scheduler runs it daily."""
from __future__ import annotations

import json
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.recurring_invoice import RecurringInvoice
from app.schemas.invoice import InvoiceItemCreate
from app.services import recurring_invoice_service as svc
from app.services.audit_service import log_audit_event

router = APIRouter(prefix="/recurring-invoices", tags=["recurring-invoices"])


class RecurringInvoiceCreate(BaseModel):
    name: str | None = Field(None, max_length=256)
    entity_id: UUID
    currency: str = "IRR"
    description: str | None = None
    amount: int = Field(0, ge=0)
    items: list[InvoiceItemCreate] = Field(default_factory=list)
    frequency: str = Field("monthly", description="weekly | monthly | quarterly | yearly")
    calendar: str | None = Field(None, description="gregorian | jalali; default by company locale")
    start_date: date
    end_date: date | None = None
    max_occurrences: int | None = Field(None, ge=1, le=1000)
    terms_days: int = Field(30, ge=0, le=365)
    issue_status: str = Field("issued", description="issued | draft")
    auto_send: bool = False
    generate_now: bool = Field(True, description="Issue occurrences already due (start today → first invoice now)")


class RecurringInvoiceUpdate(BaseModel):
    name: str | None = Field(None, max_length=256)
    description: str | None = None
    amount: int | None = Field(None, ge=0)
    items: list[InvoiceItemCreate] | None = None
    frequency: str | None = None
    calendar: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    max_occurrences: int | None = Field(None, ge=1, le=1000)
    terms_days: int | None = Field(None, ge=0, le=365)
    issue_status: str | None = None
    auto_send: bool | None = None
    status: str | None = Field(None, description="active | paused")


def _read(db: Session, t: RecurringInvoice) -> dict:
    ent = db.get(Entity, t.entity_id)
    last = db.get(Invoice, t.last_invoice_id) if t.last_invoice_id else None
    return {
        "id": str(t.id), "name": t.name, "entity_id": str(t.entity_id), "entity_name": ent.name if ent else None,
        "entity_email": (ent.email if ent else None), "currency": t.currency, "description": t.description,
        "amount": int(t.amount or 0), "items": svc.template_items(t),
        "total": svc.template_total(db, t, t.next_run_date),
        "frequency": t.frequency, "calendar": t.calendar, "start_date": t.start_date.isoformat(),
        "end_date": t.end_date.isoformat() if t.end_date else None, "max_occurrences": t.max_occurrences,
        "next_run_date": t.next_run_date.isoformat(), "occurrences": int(t.occurrences or 0),
        "terms_days": int(t.terms_days or 0), "issue_status": t.issue_status, "auto_send": bool(t.auto_send),
        "status": t.status, "last_invoice_id": str(t.last_invoice_id) if t.last_invoice_id else None,
        "last_invoice_number": last.number if last else None,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None, "last_error": t.last_error,
    }


def _load(db: Session, template_id: UUID) -> RecurringInvoice:
    t = db.get(RecurringInvoice, template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Recurring invoice not found.")
    return t


def _check_choice(value: str | None, allowed: tuple, label: str) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v not in allowed:
        raise HTTPException(status_code=422, detail=f"{label} must be one of {', '.join(allowed)}.")
    return v


def _default_calendar(db: Session) -> str:
    from app.services.locale_service import get_reporting_locale
    return "jalali" if get_reporting_locale(db) == "ir" else "gregorian"


def _items_json(items: list[InvoiceItemCreate]) -> str:
    return json.dumps([it.model_dump(mode="json") for it in items])


@router.get("")
def list_recurring_invoices(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(RecurringInvoice).order_by(RecurringInvoice.next_run_date)).scalars().all()
    return [_read(db, t) for t in rows]


@router.get("/{template_id}")
def get_recurring_invoice(template_id: UUID, db: Session = Depends(get_db)) -> dict:
    return _read(db, _load(db, template_id))


@router.get("/{template_id}/invoices")
def recurring_invoice_history(template_id: UUID, db: Session = Depends(get_db)) -> list[dict]:
    _load(db, template_id)
    rows = db.execute(select(Invoice).where(Invoice.recurring_invoice_id == template_id)
                      .order_by(Invoice.issue_date)).scalars().all()
    return [{"id": str(i.id), "number": i.number, "issue_date": i.issue_date.isoformat(),
             "due_date": i.due_date.isoformat(), "amount": int(i.amount or 0), "status": i.status} for i in rows]


@router.post("", status_code=201)
def create_recurring_invoice(payload: RecurringInvoiceCreate, db: Session = Depends(get_db)) -> dict:
    freq = _check_choice(payload.frequency, svc.FREQUENCIES, "frequency")
    cal = _check_choice(payload.calendar, svc.CALENDARS, "calendar") or _default_calendar(db)
    status = _check_choice(payload.issue_status, ("issued", "draft"), "issue_status")
    ent = db.get(Entity, payload.entity_id)
    if ent is None:
        raise HTTPException(status_code=422, detail="Customer entity not found.")
    if payload.end_date is not None and payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="end_date is before start_date.")
    if not payload.items and int(payload.amount or 0) <= 0:
        raise HTTPException(status_code=422, detail="Give a positive amount or at least one priced line.")
    t = RecurringInvoice(
        name=(payload.name or "").strip() or f"{ent.name} — {freq}", entity_id=payload.entity_id,
        currency=(payload.currency or "IRR").strip().upper(), description=(payload.description or "").strip() or None,
        amount=int(payload.amount or 0), items=_items_json(payload.items), frequency=freq, calendar=cal,
        start_date=payload.start_date, end_date=payload.end_date, max_occurrences=payload.max_occurrences,
        next_run_date=payload.start_date, occurrences=0, terms_days=payload.terms_days, issue_status=status,
        auto_send=bool(payload.auto_send), status="active",
    )
    db.add(t)
    db.flush()
    if svc.template_total(db, t, t.start_date) <= 0:
        db.rollback()
        raise HTTPException(status_code=422, detail="The template bills nothing; check the lines.")
    log_audit_event(db, action="create", entity_type="recurring_invoice", entity_id=str(t.id),
                    detail=f"Recurring invoice '{t.name}' ({freq}, from {t.start_date.isoformat()})"
                           + (", auto-send" if t.auto_send else ""))
    db.commit()
    result = None
    if payload.generate_now and t.start_date <= date.today():
        result = svc.generate_due(db, template_id=t.id)
    out = _read(db, _load(db, t.id))
    out["generated"] = result
    return out


@router.patch("/{template_id}")
def update_recurring_invoice(template_id: UUID, payload: RecurringInvoiceUpdate, db: Session = Depends(get_db)) -> dict:
    t = _load(db, template_id)
    if t.status == "ended" and payload.status == "active":
        raise HTTPException(status_code=409, detail="This schedule has ended; create a new one.")
    schedule = {k for k in ("frequency", "calendar", "start_date") if getattr(payload, k) is not None}
    if schedule and int(t.occurrences or 0) > 0:
        raise HTTPException(status_code=409, detail="The schedule already issued invoices; its frequency, calendar and start can't change. Create a new one.")
    freq = _check_choice(payload.frequency, svc.FREQUENCIES, "frequency")
    cal = _check_choice(payload.calendar, svc.CALENDARS, "calendar")
    issue = _check_choice(payload.issue_status, ("issued", "draft"), "issue_status")
    status = _check_choice(payload.status, ("active", "paused"), "status")
    start = payload.start_date or t.start_date
    end = payload.end_date if payload.end_date is not None else t.end_date
    if end is not None and end < start:
        raise HTTPException(status_code=422, detail="end_date is before start_date.")
    if payload.name is not None:
        t.name = payload.name.strip() or t.name
    if payload.description is not None:
        t.description = payload.description.strip() or None
    if payload.amount is not None:
        t.amount = int(payload.amount)
    if payload.items is not None:
        t.items = _items_json(payload.items)
    if freq:
        t.frequency = freq
    if cal:
        t.calendar = cal
    if payload.start_date is not None:
        t.start_date = payload.start_date
        t.next_run_date = payload.start_date
    t.end_date = end
    if payload.max_occurrences is not None:
        t.max_occurrences = payload.max_occurrences
    if payload.terms_days is not None:
        t.terms_days = payload.terms_days
    if issue:
        t.issue_status = issue
    if payload.auto_send is not None:
        t.auto_send = bool(payload.auto_send)
    if status:
        t.status = status
        if status == "active" and t.next_run_date < date.today():
            # Resuming after a pause does not back-bill the paused periods.
            n = int(t.occurrences or 0)
            while svc.occurrence_date(t.start_date, t.frequency, t.calendar, n) < date.today():
                n += 1
            t.next_run_date = svc.occurrence_date(t.start_date, t.frequency, t.calendar, n)
            t.occurrences = n
    if svc.template_total(db, t, t.next_run_date) <= 0:
        db.rollback()
        raise HTTPException(status_code=422, detail="The template bills nothing; check the lines.")
    log_audit_event(db, action="update", entity_type="recurring_invoice", entity_id=str(t.id),
                    detail=f"Recurring invoice '{t.name}' updated")
    db.commit()
    return _read(db, _load(db, t.id))


@router.delete("/{template_id}", status_code=204)
def delete_recurring_invoice(template_id: UUID, db: Session = Depends(get_db)) -> None:
    """Stops the schedule. Invoices it already issued stay as they are."""
    t = _load(db, template_id)
    log_audit_event(db, action="delete", entity_type="recurring_invoice", entity_id=str(t.id),
                    detail=f"Recurring invoice '{t.name}' deleted after {t.occurrences} invoice(s)")
    for inv in db.execute(select(Invoice).where(Invoice.recurring_invoice_id == t.id)).scalars().all():
        inv.recurring_invoice_id = None
    db.delete(t)
    db.commit()
