"""Versioned inbound integration API (/api/v1) — the stable 3rd-party contract.

External tools (Jira, Trello, Toggl…) push time entries with a per-company API
key (``Authorization: Bearer <key>`` or ``X-API-Key``). The middleware resolves
the key to its company BEFORE these handlers run, so every query here is
tenant-scoped automatically and a key can never touch another company's data.

Contract highlights (see the OpenAPI schema under /docs):
- POST /api/v1/time-entries — one entry or a batch; **idempotent upsert** on
  (company, source, external_id): re-pushing updates, never duplicates.
- Unknown workers are PARKED (``pending_unmatched``) for in-app resolution —
  never silently dropped, never auto-creating people.
- Pushes into a finalized (posted/paid) payroll period, or onto an entry that
  is already invoiced/paid, are rejected with a clear reason.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.models.pay_run import PayRun, PayRunLine
from app.models.pending_time_entry import PendingTimeEntry
from app.models.time_billing import Project, TimeEntry
from app.services import time_billing_service as tbs
from app.services.audit_service import log_audit_event

router = APIRouter(prefix="/api/v1", tags=["integration-api-v1"])

MAX_BATCH = 200
MAX_DAY_HOURS = 24.0


def require_api_key(request: Request) -> None:
    """Belt-and-suspenders: these routes are only reachable through the
    API-key middleware branch, which stamps request.state.api_key."""
    if not getattr(request.state, "api_key", False):
        raise HTTPException(status_code=401, detail="A valid API key is required.")


# ---------------------------------------------------------------------------
# Schemas (the integrator contract)
# ---------------------------------------------------------------------------


class TimeEntryPush(BaseModel):
    """One worklog from an external system."""
    external_id: str = Field(..., min_length=1, max_length=128,
                             description="The source system's worklog id (idempotency key)")
    source: str = Field(..., min_length=1, max_length=32,
                        description='Source system, e.g. "jira", "trello", "toggl"')
    worker: str = Field(..., min_length=1, max_length=256,
                        description="Employee email or code to map the worker")
    date: date
    hours: float = Field(..., gt=0, le=MAX_DAY_HOURS)
    description: str | None = None
    project: str | None = Field(None, description="Project name (optional)")
    client: str | None = Field(None, description="Client name (optional)")
    entry_type: str = Field("work", description="work | leave | travel | unpaid")
    billable: bool | None = Field(None, description="Defaults to true when a client/project maps")


class TimeEntryPushBatch(BaseModel):
    entries: list[TimeEntryPush] = Field(..., min_length=1, max_length=MAX_BATCH)


class PushResult(BaseModel):
    external_id: str
    source: str
    status: str  # mapped | pending_unmatched | rejected
    id: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _match_worker(db: Session, ref: str) -> Entity | None:
    """Map a worker reference (email or code) to an employee/contractor entity.
    Case-insensitive; never creates anyone."""
    ref = (ref or "").strip()
    if not ref:
        return None
    q = select(Entity).where(
        Entity.type.in_(("employee", "supplier")),
        func.lower(Entity.email) == ref.lower(),
    )
    ent = db.execute(q).scalars().first()
    if ent is None:
        ent = db.execute(
            select(Entity).where(
                Entity.type.in_(("employee", "supplier")),
                func.lower(Entity.code) == ref.lower(),
            )
        ).scalars().first()
    return ent


def _match_client(db: Session, name: str | None) -> Entity | None:
    if not name:
        return None
    return db.execute(
        select(Entity).where(
            Entity.type.in_(("client", "customer")),
            func.lower(Entity.name) == name.strip().lower(),
        )
    ).scalars().first()


def _match_project(db: Session, name: str | None) -> Project | None:
    if not name:
        return None
    return db.execute(
        select(Project).where(func.lower(Project.name) == name.strip().lower())
    ).scalars().first()


def _in_finalized_period(db: Session, entity_id, on: date) -> bool:
    """True when a POSTED/PAID pay run covering this date includes this worker
    — that period is immutable, so new pushes into it are rejected."""
    return bool(db.execute(
        select(PayRun.id)
        .join(PayRunLine, PayRunLine.run_id == PayRun.id)
        .where(
            PayRun.status.in_(("posted", "paid")),
            PayRun.period_start <= on,
            PayRun.period_end >= on,
            PayRunLine.entity_id == entity_id,
        )
    ).first())


def _day_total(db: Session, entity_id, on: date, exclude_id=None) -> float:
    q = select(TimeEntry).where(TimeEntry.employee_id == entity_id, TimeEntry.work_date == on)
    total = 0.0
    for e in db.execute(q).scalars().all():
        if exclude_id is not None and e.id == exclude_id:
            continue
        total += float(e.hours or 0)
    return total


def _park(db: Session, item: TimeEntryPush, reason: str) -> PendingTimeEntry:
    """Park (or refresh) an unmatched push — idempotent per (source, external_id)."""
    row = db.execute(
        select(PendingTimeEntry).where(
            PendingTimeEntry.source == item.source,
            PendingTimeEntry.external_id == item.external_id,
        )
    ).scalars().first()
    if row is None:
        row = PendingTimeEntry(source=item.source, external_id=item.external_id,
                               worker_ref=item.worker)
        db.add(row)
    row.worker_ref = item.worker
    row.client_ref = item.client
    row.project_ref = item.project
    row.work_date = item.date
    row.hours = item.hours
    row.description = item.description
    row.entry_type = (item.entry_type or "work").lower()
    row.billable = bool(item.billable) if item.billable is not None else False
    row.status = "pending"
    row.reason = reason[:256]
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _process_one(db: Session, item: TimeEntryPush) -> PushResult:
    et = (item.entry_type or "work").strip().lower()
    if et not in tbs.ENTRY_TYPES:
        return PushResult(external_id=item.external_id, source=item.source, status="rejected",
                          reason=f"entry_type must be one of {', '.join(tbs.ENTRY_TYPES)}")

    worker = _match_worker(db, item.worker)
    if worker is None:
        _park(db, item, f"No employee matches '{item.worker}' (email or code)")
        return PushResult(external_id=item.external_id, source=item.source,
                          status="pending_unmatched",
                          reason=f"No employee matches '{item.worker}'; parked for in-app resolution")

    # Existing entry for (source, external_id) → idempotent update.
    existing = db.execute(
        select(TimeEntry).where(
            TimeEntry.source == item.source,
            TimeEntry.external_id == item.external_id,
        )
    ).scalars().first()
    if existing is not None:
        if existing.status == "invoiced":
            return PushResult(external_id=item.external_id, source=item.source, status="rejected",
                              id=str(existing.id),
                              reason="Entry is already invoiced to the client; void the invoice first")
        if existing.payroll_run_id is not None or existing.payroll_status == "paid":
            return PushResult(external_id=item.external_id, source=item.source, status="rejected",
                              id=str(existing.id),
                              reason="Entry is settled in a pay run; void the run first")

    if _in_finalized_period(db, worker.id, item.date):
        return PushResult(external_id=item.external_id, source=item.source, status="rejected",
                          reason=f"{item.date.isoformat()} falls in a finalized (posted) payroll "
                                 "period for this employee")

    # Overlap/plausibility: a worker's day can't exceed 24h in total.
    day = _day_total(db, worker.id, item.date,
                     exclude_id=(existing.id if existing is not None else None))
    if day + float(item.hours) > MAX_DAY_HOURS:
        return PushResult(external_id=item.external_id, source=item.source, status="rejected",
                          reason=f"Would exceed {MAX_DAY_HOURS:g}h on {item.date.isoformat()} "
                                 f"({day:g}h already logged)")

    project = _match_project(db, item.project)
    client = _match_client(db, item.client)
    client_id = project.client_id if project is not None else (client.id if client else None)
    billable = item.billable if item.billable is not None else (client_id is not None)
    if client_id is None:
        billable = False

    if existing is None:
        entry = TimeEntry(
            employee_id=worker.id, client_id=client_id,
            project_id=(project.id if project else None),
            work_date=item.date, hours=item.hours, description=item.description,
            billable=billable, status="unbilled",
            entry_type=et, payable=tbs.default_payable(et), payroll_status="unpaid",
            source=item.source, external_id=item.external_id,
            created_by=f"api:{item.source}",
        )
        db.add(entry)
        db.flush()
        action = "create"
    else:
        entry = existing
        entry.employee_id = worker.id
        entry.client_id = client_id
        entry.project_id = project.id if project else None
        entry.work_date = item.date
        entry.hours = item.hours
        entry.description = item.description
        entry.billable = billable
        entry.entry_type = et
        entry.payable = tbs.default_payable(et)
        db.flush()
        action = "update"

    # A previously parked row with this id is superseded by the mapped entry.
    parked = db.execute(
        select(PendingTimeEntry).where(
            PendingTimeEntry.source == item.source,
            PendingTimeEntry.external_id == item.external_id,
            PendingTimeEntry.status == "pending",
        )
    ).scalars().first()
    if parked is not None:
        parked.status = "resolved"
        parked.resolved_entry_id = entry.id

    log_audit_event(db, action, "time_entry", entity_id=str(entry.id),
                    detail=f"{item.source} push {item.external_id}: {item.hours}h for {worker.name}")
    return PushResult(external_id=item.external_id, source=item.source,
                      status="mapped", id=str(entry.id))


@router.post("/time-entries", dependencies=[Depends(require_api_key)])
def push_time_entries(
    payload: TimeEntryPush | TimeEntryPushBatch,
    db: Session = Depends(get_db),
) -> dict:
    """Create or update time entries — one object or ``{"entries": [...]}``.

    Idempotent per ``(source, external_id)``: re-pushing the same worklog
    updates it in place, never duplicates. Per-entry result statuses:
    ``mapped`` | ``pending_unmatched`` (parked for in-app resolution) |
    ``rejected`` (with a reason).
    """
    items = payload.entries if isinstance(payload, TimeEntryPushBatch) else [payload]
    results = [_process_one(db, item) for item in items]
    db.commit()
    return {
        "results": [r.model_dump() for r in results],
        "summary": {
            s: sum(1 for r in results if r.status == s)
            for s in ("mapped", "pending_unmatched", "rejected")
        },
    }


@router.get("/time-entries", dependencies=[Depends(require_api_key)])
def list_pushed_entries(
    source: str | None = None,
    worker: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """List pushed entries (mapped + parked) for reconciliation."""
    q = select(TimeEntry).where(TimeEntry.source.is_not(None))
    if source:
        q = q.where(TimeEntry.source == source)
    if date_from:
        q = q.where(TimeEntry.work_date >= date_from)
    if date_to:
        q = q.where(TimeEntry.work_date <= date_to)
    entries = db.execute(q.order_by(TimeEntry.work_date.desc())).scalars().all()
    if worker:
        w = _match_worker(db, worker)
        entries = [e for e in entries if w and e.employee_id == w.id]

    pq = select(PendingTimeEntry)
    if source:
        pq = pq.where(PendingTimeEntry.source == source)
    if status:
        pq = pq.where(PendingTimeEntry.status == status)
    parked = db.execute(pq.order_by(PendingTimeEntry.created_at.desc())).scalars().all()

    return {
        "mapped": [{
            "id": str(e.id), "source": e.source, "external_id": e.external_id,
            "employee_id": str(e.employee_id), "date": e.work_date.isoformat(),
            "hours": float(e.hours or 0), "entry_type": e.entry_type,
            "billable": bool(e.billable), "billing_status": e.status,
            "payroll_status": e.payroll_status,
        } for e in entries],
        "pending": [{
            "id": str(p.id), "source": p.source, "external_id": p.external_id,
            "worker": p.worker_ref, "date": p.work_date.isoformat(),
            "hours": float(p.hours or 0), "status": p.status, "reason": p.reason,
        } for p in parked],
    }


@router.delete("/time-entries/by-external/{source}/{external_id:path}",
               dependencies=[Depends(require_api_key)])
def delete_pushed_entry(source: str, external_id: str, db: Session = Depends(get_db)) -> dict:
    """Remove a worklog that was deleted upstream. Blocked once the entry is
    invoiced or settled in a pay run (void those first)."""
    entry = db.execute(
        select(TimeEntry).where(TimeEntry.source == source, TimeEntry.external_id == external_id)
    ).scalars().first()
    parked = db.execute(
        select(PendingTimeEntry).where(
            PendingTimeEntry.source == source, PendingTimeEntry.external_id == external_id,
            PendingTimeEntry.status == "pending",
        )
    ).scalars().first()
    if entry is None and parked is None:
        raise HTTPException(status_code=404, detail="No entry with that source/external_id.")
    if entry is not None:
        if entry.status == "invoiced":
            raise HTTPException(status_code=409, detail="Entry is invoiced; void the invoice first.")
        if entry.payroll_run_id is not None or entry.payroll_status == "paid":
            raise HTTPException(status_code=409, detail="Entry is settled in a pay run; void the run first.")
        log_audit_event(db, "delete", "time_entry", entity_id=str(entry.id),
                        detail=f"{source} push {external_id} deleted upstream")
        db.delete(entry)
    if parked is not None:
        parked.status = "rejected"
        parked.reason = "Deleted upstream"
    db.commit()
    return {"ok": True}
