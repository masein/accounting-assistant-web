"""Time tracking & time-based billing API: projects, billable rates, time
entries (the timesheet grid), unbilled summaries, and creating an AR invoice
from unbilled time (+ a branded time-invoice PDF).
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.time_billing import BillingRateOverride, Project, TimeEntry
from app.services import time_billing_service as tbs
from app.services.audit_service import log_audit_event
from app.services.time_billing_service import TimeBillingError

router = APIRouter(prefix="/time", tags=["time-tracking"])

_date = date


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    client_id: UUID
    name: str = Field(..., min_length=1, max_length=256)
    code: str | None = None
    default_currency: str | None = None


class RateOverrideCreate(BaseModel):
    employee_id: UUID
    rate: float = Field(..., ge=0)
    client_id: UUID | None = None
    project_id: UUID | None = None
    currency: str | None = None


class TimeEntryCreate(BaseModel):
    employee_id: UUID
    client_id: UUID | None = None
    project_id: UUID | None = None
    work_date: _date
    hours: float = Field(..., gt=0)
    description: str | None = None
    billable: bool = True
    # work | leave | travel | unpaid — payroll behaviour (see time_billing model).
    entry_type: str = "work"
    # Counts toward employee pay; default derived from entry_type (unpaid → False).
    payable: bool | None = None


class TimeEntryUpdate(BaseModel):
    hours: float | None = Field(None, gt=0)
    description: str | None = None
    billable: bool | None = None
    project_id: UUID | None = None
    work_date: _date | None = None
    entry_type: str | None = None
    payable: bool | None = None


class InvoiceFromTimeRequest(BaseModel):
    client_id: UUID
    project_id: UUID | None = None
    date_from: _date | None = None
    date_to: _date | None = None
    include_entry_ids: list[UUID] | None = None
    invoice_date: _date | None = None
    due_date: _date | None = None
    manual_lines: list[dict] | None = None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _name(db: Session, eid) -> str | None:
    e = db.get(Entity, eid) if eid else None
    return e.name if e else None


def _project_read(p: Project, db: Session) -> dict:
    return {
        "id": str(p.id), "client_id": str(p.client_id), "client_name": _name(db, p.client_id),
        "name": p.name, "code": p.code, "status": p.status,
        "default_currency": p.default_currency,
    }


def _entry_read(e: TimeEntry, db: Session) -> dict:
    proj = db.get(Project, e.project_id) if e.project_id else None
    rate = (
        tbs.resolve_billable_rate(db, e.employee_id, e.client_id, e.project_id)
        if e.client_id else None
    )
    return {
        "id": str(e.id),
        "employee_id": str(e.employee_id), "employee_name": _name(db, e.employee_id),
        "client_id": (str(e.client_id) if e.client_id else None),
        "client_name": _name(db, e.client_id),
        "project_id": (str(e.project_id) if e.project_id else None),
        "project_name": (proj.name if proj else None),
        "work_date": e.work_date.isoformat(), "hours": float(e.hours or 0),
        "description": e.description, "billable": bool(e.billable), "status": e.status,
        "invoice_id": (str(e.invoice_id) if e.invoice_id else None),
        "entry_type": e.entry_type or "work",
        "payable": bool(e.payable),
        "payroll_status": e.payroll_status or "unpaid",
        "payroll_run_id": (str(e.payroll_run_id) if e.payroll_run_id else None),
        "source": e.source,
        "external_id": e.external_id,
        "rate": (rate["rate"] if rate else None),
        "rate_source": (rate["source"] if rate else None),
        "rate_snapshot": (float(e.rate_snapshot) if e.rate_snapshot is not None else None),
        "currency": e.currency,
        # Locked by either settlement track: client-invoiced OR in a pay run.
        "locked": e.status != "unbilled" or e.payroll_run_id is not None,
    }


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects")
def list_projects(client_id: UUID | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = select(Project).order_by(Project.name)
    if client_id is not None:
        q = q.where(Project.client_id == client_id)
    return [_project_read(p, db) for p in db.execute(q).scalars().all()]


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> dict:
    client = db.get(Entity, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    if client.type != "client":
        raise HTTPException(status_code=422, detail="A project must belong to a client entity.")
    p = Project(client_id=payload.client_id, name=payload.name.strip(),
                code=(payload.code or None), default_currency=(payload.default_currency or None))
    db.add(p)
    log_audit_event(db, action="create", entity_type="project", entity_id=str(p.id),
                    detail=f"Project {payload.name}")
    db.commit()
    db.refresh(p)
    return _project_read(p, db)


# ---------------------------------------------------------------------------
# Billable rates
# ---------------------------------------------------------------------------


@router.get("/rates")
def list_rates(employee_id: UUID | None = None, db: Session = Depends(get_db)) -> list[dict]:
    q = select(BillingRateOverride)
    if employee_id is not None:
        q = q.where(BillingRateOverride.employee_id == employee_id)
    out = []
    for ov in db.execute(q).scalars().all():
        out.append({
            "id": str(ov.id), "employee_id": str(ov.employee_id),
            "employee_name": _name(db, ov.employee_id),
            "client_id": (str(ov.client_id) if ov.client_id else None),
            "project_id": (str(ov.project_id) if ov.project_id else None),
            "rate": float(ov.rate or 0), "currency": ov.currency,
        })
    return out


@router.post("/rates", status_code=201)
def set_rate(payload: RateOverrideCreate, db: Session = Depends(get_db)) -> dict:
    worker = db.get(Entity, payload.employee_id)
    if not worker or worker.type not in ("employee", "supplier"):
        raise HTTPException(status_code=422, detail="Billable rates apply to an employee or contractor (supplier).")
    scope = tbs.set_billable_rate(
        db, employee_id=payload.employee_id, rate=payload.rate,
        client_id=payload.client_id, project_id=payload.project_id, currency=payload.currency,
    )
    log_audit_event(db, action="upsert", entity_type="billing_rate", entity_id=str(payload.employee_id),
                    detail=f"Billable rate {payload.rate} ({scope})")
    db.commit()
    return {"ok": True, "scope": scope, "rate": payload.rate}


# ---------------------------------------------------------------------------
# Time entries (the grid)
# ---------------------------------------------------------------------------


@router.get("/entries")
def list_entries(client_id: UUID | None = None, project_id: UUID | None = None,
                 employee_id: UUID | None = None, date_from: _date | None = None,
                 date_to: _date | None = None, status: str | None = None,
                 db: Session = Depends(get_db)) -> list[dict]:
    q = select(TimeEntry).order_by(TimeEntry.work_date.desc(), TimeEntry.created_at.desc())
    if client_id is not None:
        q = q.where(TimeEntry.client_id == client_id)
    if project_id is not None:
        q = q.where(TimeEntry.project_id == project_id)
    if employee_id is not None:
        q = q.where(TimeEntry.employee_id == employee_id)
    if date_from is not None:
        q = q.where(TimeEntry.work_date >= date_from)
    if date_to is not None:
        q = q.where(TimeEntry.work_date <= date_to)
    if status:
        q = q.where(TimeEntry.status == status.strip().lower())
    restricted, own = _time_own_scope()
    if restricted:
        if not own:
            return []
        q = q.where(TimeEntry.employee_id == UUID(str(own)))
    return [_entry_read(e, db) for e in db.execute(q).scalars().all()]


def _time_own_scope():
    """Self-service (Employee) callers only see/log their own time."""
    from app.core.permissions import Perm, own_scope
    from app.core.request_context import get_current_actor
    return own_scope(get_current_actor(), Perm.BOOKS_READ)


@router.post("/entries", status_code=201)
def create_entry(payload: TimeEntryCreate, db: Session = Depends(get_db)) -> dict:
    # A self-service caller may only log time for their own employee entity.
    restricted, own = _time_own_scope()
    if restricted and str(payload.employee_id) != str(own or ""):
        raise HTTPException(status_code=403, detail="You can only log your own time.")
    entry_type = (payload.entry_type or "work").strip().lower()
    if entry_type not in tbs.ENTRY_TYPES:
        raise HTTPException(
            status_code=422, detail=f"entry_type must be one of {', '.join(tbs.ENTRY_TYPES)}."
        )
    client_id = payload.client_id
    if payload.project_id is not None:
        proj = db.get(Project, payload.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found.")
        client_id = proj.client_id  # a project implies its client
    # A client is only needed for BILLABLE time. Payroll-only entries (leave,
    # internal work) have no client and are never billable.
    billable = payload.billable
    if client_id is None:
        billable = False
    worker = db.get(Entity, payload.employee_id)
    if not worker or worker.type not in ("employee", "supplier"):
        raise HTTPException(status_code=422, detail="Time is logged for an employee or contractor (supplier).")

    payable = payload.payable if payload.payable is not None else tbs.default_payable(entry_type)
    e = TimeEntry(
        employee_id=payload.employee_id, client_id=client_id, project_id=payload.project_id,
        work_date=payload.work_date, hours=payload.hours, description=payload.description,
        billable=billable, status="unbilled",
        entry_type=entry_type, payable=payable, payroll_status="unpaid",
    )
    db.add(e)
    log_audit_event(db, action="create", entity_type="time_entry", entity_id=str(e.id),
                    detail=f"Logged {payload.hours}h")
    db.commit()
    db.refresh(e)
    return _entry_read(e, db)


@router.patch("/entries/{entry_id}")
def update_entry(entry_id: UUID, payload: TimeEntryUpdate, db: Session = Depends(get_db)) -> dict:
    e = db.get(TimeEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Time entry not found.")
    restricted, own = _time_own_scope()
    if restricted and str(e.employee_id) != str(own or ""):
        raise HTTPException(status_code=404, detail="Time entry not found.")
    if e.status != "unbilled":
        raise HTTPException(
            status_code=409,
            detail="This time entry is invoiced and locked. Void its invoice to edit it.",
        )
    if e.payroll_run_id is not None:
        raise HTTPException(
            status_code=409,
            detail="This time entry is in a pay run and locked. Void the pay run to edit it.",
        )
    if payload.hours is not None:
        e.hours = payload.hours
    if payload.description is not None:
        e.description = payload.description
    if payload.billable is not None:
        e.billable = payload.billable if e.client_id else False
    if payload.work_date is not None:
        e.work_date = payload.work_date
    if payload.entry_type is not None:
        et = payload.entry_type.strip().lower()
        if et not in tbs.ENTRY_TYPES:
            raise HTTPException(
                status_code=422, detail=f"entry_type must be one of {', '.join(tbs.ENTRY_TYPES)}."
            )
        e.entry_type = et
        if payload.payable is None:
            e.payable = tbs.default_payable(et)
    if payload.payable is not None:
        e.payable = payload.payable
    if payload.project_id is not None:
        proj = db.get(Project, payload.project_id)
        if proj:
            e.project_id = payload.project_id
            e.client_id = proj.client_id
    db.commit()
    db.refresh(e)
    return _entry_read(e, db)


@router.post("/entries/{entry_id}/write-off")
def write_off_entry(entry_id: UUID, db: Session = Depends(get_db)) -> dict:
    e = db.get(TimeEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Time entry not found.")
    if e.status == "invoiced":
        raise HTTPException(status_code=409, detail="Invoiced time is locked — void its invoice first.")
    if e.payroll_run_id is not None:
        raise HTTPException(status_code=409, detail="This time entry is in a pay run — void the pay run first.")
    e.status = "written_off"
    log_audit_event(db, action="update", entity_type="time_entry", entity_id=str(e.id),
                    detail="Time written off")
    db.commit()
    db.refresh(e)
    return _entry_read(e, db)


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: UUID, db: Session = Depends(get_db)) -> Response:
    e = db.get(TimeEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Time entry not found.")
    restricted, own = _time_own_scope()
    if restricted and str(e.employee_id) != str(own or ""):
        raise HTTPException(status_code=404, detail="Time entry not found.")
    if e.status == "invoiced":
        raise HTTPException(status_code=409, detail="Invoiced time is locked — void its invoice first.")
    if e.payroll_run_id is not None:
        raise HTTPException(status_code=409, detail="This time entry is in a pay run — void the pay run first.")
    db.delete(e)
    db.commit()
    return Response(status_code=204)


@router.get("/my-summary")
def my_summary(period_start: _date, period_end: _date, db: Session = Depends(get_db)) -> dict:
    """Self-service: MY hours for a period — required vs worked, leave,
    overtime/undertime. Resolves to the caller's linked employee entity; users
    with full payroll access may pass ?entity_id= to view any employee."""
    from app.core.permissions import Perm, own_scope
    from app.core.request_context import get_current_actor
    if period_end < period_start:
        raise HTTPException(status_code=422, detail="period_end is before period_start.")
    restricted, own = own_scope(get_current_actor(), Perm.PAYROLL_READ)
    if restricted:
        if not own:
            raise HTTPException(status_code=404, detail="No employee record linked to your account.")
        entity_id = UUID(str(own))
    else:
        actor = get_current_actor()
        own_ent = getattr(actor, "entity_id", None) if actor else None
        if not own_ent:
            raise HTTPException(status_code=404, detail="No employee record linked to your account.")
        entity_id = UUID(str(own_ent))

    from app.models.employee_pay import EmployeePayProfile
    s = tbs.payroll_hours_summary(db, entity_id, period_start, period_end)
    # Include already-settled hours in the display (run_id filter excludes them
    # from the payable sum, but "my hours" should show everything logged).
    all_q = select(TimeEntry).where(
        TimeEntry.employee_id == entity_id,
        TimeEntry.work_date >= period_start,
        TimeEntry.work_date <= period_end,
    )
    all_entries = db.execute(all_q).scalars().all()
    total_logged = sum(float(e.hours or 0) for e in all_entries)
    prof = db.execute(
        select(EmployeePayProfile).where(EmployeePayProfile.entity_id == entity_id)
    ).scalars().one_or_none()
    required = 0.0
    if prof is not None:
        required = float(
            prof.monthly_standard_hours
            if prof.monthly_standard_hours is not None
            else (prof.standard_hours or 0)
        )
    worked = s["worked_hours"]
    return {
        "entity_id": str(entity_id),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "required_hours": required,
        "worked_hours": worked,
        "leave_hours": s["leave_hours"],
        "total_logged_hours": round(total_logged, 2),
        "overtime_hours": round(max(0.0, worked - required), 2) if required > 0 else 0.0,
        "undertime_hours": round(max(0.0, required - worked), 2) if required > 0 else 0.0,
        "pay_type": (prof.pay_type if prof else None),
    }


# ---------------------------------------------------------------------------
# Parked integration pushes (unmatched /api/v1 worklogs) — employer resolution
# ---------------------------------------------------------------------------


class PendingResolvePayload(BaseModel):
    entity_id: UUID  # the employee this parked worklog belongs to


@router.get("/pending")
def list_pending_entries(status: str | None = "pending", db: Session = Depends(get_db)) -> list[dict]:
    """Parked pushes from external tools whose worker couldn't be matched."""
    from app.models.pending_time_entry import PendingTimeEntry
    q = select(PendingTimeEntry).order_by(PendingTimeEntry.created_at.desc())
    if status:
        q = q.where(PendingTimeEntry.status == status.strip().lower())
    return [{
        "id": str(p.id), "source": p.source, "external_id": p.external_id,
        "worker": p.worker_ref, "client": p.client_ref, "project": p.project_ref,
        "work_date": p.work_date.isoformat(), "hours": float(p.hours or 0),
        "description": p.description, "entry_type": p.entry_type,
        "billable": bool(p.billable), "status": p.status, "reason": p.reason,
    } for p in db.execute(q).scalars().all()]


@router.post("/pending/{pending_id}/resolve")
def resolve_pending_entry(pending_id: UUID, payload: PendingResolvePayload,
                          db: Session = Depends(get_db)) -> dict:
    """Assign a parked worklog to an employee → becomes a normal time entry
    (flows into payroll and billing like any other)."""
    from app.models.pending_time_entry import PendingTimeEntry
    p = db.get(PendingTimeEntry, pending_id)
    if not p or p.status != "pending":
        raise HTTPException(status_code=404, detail="Pending entry not found.")
    worker = db.get(Entity, payload.entity_id)
    if not worker or worker.type not in ("employee", "supplier"):
        raise HTTPException(status_code=422, detail="Resolve to an employee or contractor (supplier).")
    et = (p.entry_type or "work").lower()
    e = TimeEntry(
        employee_id=worker.id, client_id=None, project_id=None,
        work_date=p.work_date, hours=p.hours, description=p.description,
        billable=False, status="unbilled",
        entry_type=et, payable=tbs.default_payable(et), payroll_status="unpaid",
        source=p.source, external_id=p.external_id, created_by=f"api:{p.source}",
    )
    db.add(e)
    db.flush()
    p.status = "resolved"
    p.resolved_entry_id = e.id
    log_audit_event(db, action="update", entity_type="time_entry", entity_id=str(e.id),
                    detail=f"Resolved parked {p.source} push {p.external_id} to {worker.name}")
    db.commit()
    return _entry_read(e, db)


@router.post("/pending/{pending_id}/reject")
def reject_pending_entry(pending_id: UUID, db: Session = Depends(get_db)) -> dict:
    from app.models.pending_time_entry import PendingTimeEntry
    p = db.get(PendingTimeEntry, pending_id)
    if not p or p.status != "pending":
        raise HTTPException(status_code=404, detail="Pending entry not found.")
    p.status = "rejected"
    p.reason = "Rejected by employer"
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Unbilled summary + invoice from time
# ---------------------------------------------------------------------------


@router.get("/unbilled")
def unbilled_summary(client_id: UUID | None = None, db: Session = Depends(get_db)) -> dict:
    """'Ready to invoice': unbilled hours + value by client → project."""
    entries = tbs._unbilled_query(db, client_id=client_id)
    clients: dict = {}
    for e in entries:
        rate = tbs.resolve_billable_rate(db, e.employee_id, e.client_id, e.project_id)
        value = tbs._round(float(e.hours or 0) * rate["rate"]) if rate else 0
        ck = str(e.client_id)
        c = clients.setdefault(ck, {
            "client_id": ck, "client_name": _name(db, e.client_id),
            "hours": 0.0, "value": 0, "oldest": e.work_date.isoformat(),
            "currency": (rate["currency"] if rate else tbs.company_currency(db)),
            "projects": {},
        })
        c["hours"] += float(e.hours or 0)
        c["value"] += value
        c["oldest"] = min(c["oldest"], e.work_date.isoformat())
        pk = (str(e.project_id) if e.project_id else "general")
        proj = db.get(Project, e.project_id) if e.project_id else None
        pr = c["projects"].setdefault(pk, {
            "project_id": (str(e.project_id) if e.project_id else None),
            "project_name": (proj.name if proj else "General"),
            "hours": 0.0, "value": 0,
        })
        pr["hours"] += float(e.hours or 0)
        pr["value"] += value
    out = []
    for c in clients.values():
        c["hours"] = round(c["hours"], 2)
        c["projects"] = sorted(c["projects"].values(), key=lambda p: (p["project_name"] or "").lower())
        for p in c["projects"]:
            p["hours"] = round(p["hours"], 2)
        out.append(c)
    out.sort(key=lambda c: (c["client_name"] or "").lower())
    return {"clients": out}


@router.post("/invoice-preview")
def invoice_preview(payload: InvoiceFromTimeRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return tbs.build_preview(
            db, client_id=payload.client_id, project_id=payload.project_id,
            date_from=payload.date_from, date_to=payload.date_to,
            include_entry_ids=payload.include_entry_ids, invoice_date=payload.invoice_date,
            due_date=payload.due_date, manual_lines=payload.manual_lines,
        )
    except TimeBillingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/invoice", status_code=201)
def create_time_invoice(payload: InvoiceFromTimeRequest, db: Session = Depends(get_db)) -> dict:
    try:
        inv, preview = tbs.create_invoice_from_time(
            db, client_id=payload.client_id, project_id=payload.project_id,
            date_from=payload.date_from, date_to=payload.date_to,
            include_entry_ids=payload.include_entry_ids, invoice_date=payload.invoice_date,
            due_date=payload.due_date, manual_lines=payload.manual_lines,
        )
    except TimeBillingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "invoice_id": str(inv.id), "number": inv.number, "amount": int(inv.amount or 0),
        "currency": inv.currency, "status": inv.status, "preview": preview,
        "pdf_url": f"/time/invoice/{inv.id}/pdf",
    }


# ---------------------------------------------------------------------------
# Branded time-invoice PDF (grouped by project) + timesheet appendix
# ---------------------------------------------------------------------------


@router.get("/invoice/{invoice_id}/pdf")
def time_invoice_pdf(invoice_id: UUID, db: Session = Depends(get_db)) -> Response:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    client = db.get(Entity, inv.entity_id) if inv.entity_id else None
    entries = db.execute(
        select(TimeEntry).where(TimeEntry.invoice_id == inv.id).order_by(TimeEntry.work_date)
    ).scalars().all()

    # Rebuild the grouped view from the billed entries (project → employee).
    cur = inv.currency or tbs.company_currency(db)

    def money(n: int) -> str:
        return f"{int(n):,} {cur}"

    groups: dict = {}
    for e in entries:
        proj = db.get(Project, e.project_id) if e.project_id else None
        pname = proj.name if proj else "General"
        worker = db.get(Entity, e.employee_id)
        wkey = (pname, worker.name if worker else "—")
        g = groups.setdefault(wkey, {"hours": 0.0, "rate": float(e.rate_snapshot or 0)})
        g["hours"] += float(e.hours or 0)

    # Branded HTML→PDF engine (logo, brand colour, party cards, amount-in-words,
    # locale-aware). Legacy reportlab layout kept only as a defensive fallback.
    try:
        from app.services.documents import render_time_invoice_pdf
        group_list = [
            {"project": pname, "worker": wname, "hours": g["hours"], "rate": g["rate"],
             "amount": tbs._round(g["hours"] * g["rate"])}
            for (pname, wname), g in sorted(groups.items())
        ]
        period = (entries[0].work_date, entries[-1].work_date) if entries else None
        pdf = render_time_invoice_pdf(db, inv, client, group_list, period)
        return Response(
            content=pdf, media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="invoice-{inv.number}.pdf"'},
        )
    except Exception:  # pragma: no cover - defensive fallback
        import logging
        logging.getLogger(__name__).exception("branded time invoice PDF failed; using legacy layout")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    m = 16 * mm
    primary = colors.HexColor("#0f766e")
    ink = colors.HexColor("#0f172a")
    muted = colors.HexColor("#475569")

    c.setFillColor(primary)
    c.roundRect(m, ph - 48 * mm, pw - 2 * m, 32 * mm, 7, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(m + 8 * mm, ph - 30 * mm, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(m + 8 * mm, ph - 36 * mm, "Accounting Assistant")
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(pw - m - 8 * mm, ph - 28 * mm, f"No. {inv.number}")
    c.setFont("Helvetica", 9)
    c.drawRightString(pw - m - 8 * mm, ph - 34 * mm,
                      f"Issued {inv.issue_date.isoformat()} · Due {inv.due_date.isoformat()}")

    y = ph - 62 * mm
    c.setFillColor(muted)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(m, y, "BILL TO")
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(m, y - 6 * mm, (client.name if client else "—"))
    if entries:
        c.setFillColor(muted)
        c.setFont("Helvetica", 9)
        c.drawString(m, y - 12 * mm,
                     f"Period: {entries[0].work_date.isoformat()} → {entries[-1].work_date.isoformat()}")

    # Line items grouped by project.
    y -= 24 * mm
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(m, y, "Description")
    c.drawRightString(pw - m - 60 * mm, y, "Hours")
    c.drawRightString(pw - m - 30 * mm, y, "Rate")
    c.drawRightString(pw - m, y, "Amount")
    y -= 3 * mm
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.line(m, y, pw - m, y)
    y -= 6 * mm

    last_proj = None
    for (pname, wname), g in sorted(groups.items()):
        if pname != last_proj:
            c.setFillColor(primary)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(m, y, pname)
            y -= 6 * mm
            last_proj = pname
        amt = tbs._round(g["hours"] * g["rate"])
        c.setFillColor(ink)
        c.setFont("Helvetica", 9)
        c.drawString(m + 4 * mm, y, wname[:60])
        c.drawRightString(pw - m - 60 * mm, y, f"{round(g['hours'], 2)}")
        c.drawRightString(pw - m - 30 * mm, y, f"{g['rate']:g}")
        c.drawRightString(pw - m, y, money(amt))
        y -= 6 * mm
        if y < 40 * mm:
            c.showPage()
            y = ph - m

    # Totals.
    y -= 4 * mm
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.line(pw - m - 70 * mm, y, pw - m, y)
    y -= 7 * mm
    subtotal, tax, total = _split_totals(db, inv)
    c.setFont("Helvetica", 10)
    c.setFillColor(muted)
    c.drawRightString(pw - m - 30 * mm, y, "Subtotal")
    c.setFillColor(ink)
    c.drawRightString(pw - m, y, money(subtotal))
    y -= 6 * mm
    c.setFillColor(muted)
    c.drawRightString(pw - m - 30 * mm, y, "VAT")
    c.setFillColor(ink)
    c.drawRightString(pw - m, y, money(tax))
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(primary)
    c.drawRightString(pw - m - 30 * mm, y, "Total")
    c.drawRightString(pw - m, y, money(total))

    # Timesheet appendix.
    if entries:
        c.showPage()
        y = ph - m
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(m, y, "Timesheet detail")
        y -= 8 * mm
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(muted)
        c.drawString(m, y, "Date")
        c.drawString(m + 26 * mm, y, "Employee")
        c.drawString(m + 70 * mm, y, "Project")
        c.drawRightString(pw - m - 60 * mm, y, "Hours")
        c.drawString(pw - m - 55 * mm, y, "Description")
        y -= 5 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(ink)
        for e in entries:
            proj = db.get(Project, e.project_id) if e.project_id else None
            worker = db.get(Entity, e.employee_id)
            c.drawString(m, y, e.work_date.isoformat())
            c.drawString(m + 26 * mm, y, (worker.name if worker else "—")[:24])
            c.drawString(m + 70 * mm, y, (proj.name if proj else "General")[:18])
            c.drawRightString(pw - m - 60 * mm, y, f"{round(float(e.hours or 0), 2)}")
            c.drawString(pw - m - 55 * mm, y, (e.description or "")[:34])
            y -= 5 * mm
            if y < 20 * mm:
                c.showPage()
                y = ph - m

    c.showPage()
    c.save()
    buf.seek(0)
    return Response(
        content=buf.getvalue(), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="invoice-{inv.number}.pdf"'},
    )


def _split_totals(db: Session, inv: Invoice) -> tuple[int, int, int]:
    from app.api.invoices import _tax_breakdown
    subtotal, tax, grand = _tax_breakdown(inv)
    return subtotal, tax, grand
