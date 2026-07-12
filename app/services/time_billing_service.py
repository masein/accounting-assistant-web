"""Time-based billing: rate resolution, unbilled-time aggregation, and turning
unbilled time into a normal AR sales invoice (reusing the invoice engine).

Rate precedence: project override → client override → default override →
EmployeePayProfile.billable_rate. Amounts are rounded ONCE per invoice line
(hours × rate), consistent with how the invoice engine rounds, so double-entry
balances. Voiding an invoice un-bills its entries (handled in the void path).
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.time_billing import BillingRateOverride, Project, TimeEntry
from app.services.fx_service import get_reporting_currency
from app.services.locale_service import get_reporting_locale


class TimeBillingError(ValueError):
    """Invalid time-billing request (no rate, mixed currency, nothing to bill…)."""


def _round(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def company_currency(db: Session) -> str:
    return (get_reporting_currency(db) or "IRR").upper()


def standard_vat_code(db: Session) -> str | None:
    loc = (get_reporting_locale(db) or "").strip().lower()
    return {"uk": "UK_VAT_STANDARD", "ir": "IR_VAT_STANDARD"}.get(loc)


# ---------------------------------------------------------------------------
# Rate resolution (precedence: project → client → default → profile)
# ---------------------------------------------------------------------------


def resolve_billable_rate(db: Session, employee_id, client_id, project_id=None) -> dict | None:
    """Return ``{rate, currency, source}`` for a worker on a client/project, or
    None when no rate is configured. ``source`` ∈ project|client|default|profile."""
    cur = company_currency(db)
    overrides = db.execute(
        select(BillingRateOverride).where(BillingRateOverride.employee_id == employee_id)
    ).scalars().all()

    if project_id:
        for ov in overrides:
            if ov.project_id is not None and str(ov.project_id) == str(project_id):
                return {"rate": float(ov.rate or 0), "currency": (ov.currency or cur).upper(), "source": "project"}
    for ov in overrides:
        if ov.client_id is not None and ov.project_id is None and str(ov.client_id) == str(client_id):
            return {"rate": float(ov.rate or 0), "currency": (ov.currency or cur).upper(), "source": "client"}
    for ov in overrides:
        if ov.client_id is None and ov.project_id is None:
            return {"rate": float(ov.rate or 0), "currency": (ov.currency or cur).upper(), "source": "default"}

    prof = db.execute(
        select(EmployeePayProfile).where(EmployeePayProfile.entity_id == employee_id)
    ).scalars().first()
    if prof is not None and prof.billable_rate is not None:
        return {"rate": float(prof.billable_rate), "currency": (prof.currency or cur).upper(), "source": "profile"}
    return None


def set_billable_rate(db: Session, *, employee_id, rate: float, client_id=None,
                      project_id=None, currency: str | None = None) -> str:
    """Set a billable rate at the right scope. With no client/project it sets the
    worker's default (the pay profile for an employee, else a default override).
    Returns the scope label."""
    cur = (currency or company_currency(db)).upper()
    if project_id is not None:
        # A project implies its client.
        proj = db.get(Project, project_id)
        client_id = proj.client_id if proj else client_id
        scope, where = "project", (BillingRateOverride.project_id == project_id)
    elif client_id is not None:
        scope, where = "client", (
            (BillingRateOverride.client_id == client_id) & (BillingRateOverride.project_id.is_(None))
        )
    else:
        # Default rate: prefer the employee pay profile so it shows in payroll UI.
        prof = db.execute(
            select(EmployeePayProfile).where(EmployeePayProfile.entity_id == employee_id)
        ).scalars().first()
        if prof is not None:
            prof.billable_rate = rate
            db.flush()
            return "employee-default (pay profile)"
        scope, where = "default", (
            (BillingRateOverride.client_id.is_(None)) & (BillingRateOverride.project_id.is_(None))
        )

    existing = db.execute(
        select(BillingRateOverride).where(
            BillingRateOverride.employee_id == employee_id, where,
        )
    ).scalars().first()
    if existing is None:
        existing = BillingRateOverride(employee_id=employee_id, client_id=client_id, project_id=project_id)
        db.add(existing)
    existing.rate = rate
    existing.currency = cur
    db.flush()
    return scope


# ---------------------------------------------------------------------------
# Aggregation + preview
# ---------------------------------------------------------------------------


def _name(db: Session, entity_id) -> str:
    e = db.get(Entity, entity_id) if entity_id else None
    return e.name if e else "—"


def _unbilled_query(db: Session, *, client_id=None, project_id=None,
                    date_from=None, date_to=None, include_entry_ids=None):
    q = select(TimeEntry).where(
        TimeEntry.status == "unbilled",
        TimeEntry.billable.is_(True),
    )
    if client_id is not None:
        q = q.where(TimeEntry.client_id == client_id)
    if project_id is not None:
        q = q.where(TimeEntry.project_id == project_id)
    if date_from is not None:
        q = q.where(TimeEntry.work_date >= date_from)
    if date_to is not None:
        q = q.where(TimeEntry.work_date <= date_to)
    rows = db.execute(q.order_by(TimeEntry.work_date)).scalars().all()
    if include_entry_ids is not None:
        keep = {str(i) for i in include_entry_ids}
        rows = [r for r in rows if str(r.id) in keep]
    return rows


def build_preview(db: Session, *, client_id, project_id=None, date_from=None, date_to=None,
                  include_entry_ids=None, invoice_date=None, due_date=None,
                  manual_lines=None) -> dict:
    """Group unbilled time (project → employee), resolve rates, and compute
    subtotal/VAT/total. Raises TimeBillingError on mixed currency."""
    entries = _unbilled_query(
        db, client_id=client_id, project_id=project_id, date_from=date_from,
        date_to=date_to, include_entry_ids=include_entry_ids,
    )
    today = invoice_date or date.today()
    vat_code = standard_vat_code(db)
    vat_rate = 0.0
    if vat_code:
        from app.services.tax_rate_service import tax_rate_for
        vat_rate = float(tax_rate_for(db, vat_code, today) or 0)

    # Group (project_id, employee_id) → accumulate.
    groups: dict = {}
    currencies: set[str] = set()
    for e in entries:
        rate_info = resolve_billable_rate(db, e.employee_id, e.client_id, e.project_id)
        if rate_info is None:
            raise TimeBillingError(
                f"No billable rate set for {_name(db, e.employee_id)} — set one before invoicing."
            )
        currencies.add(rate_info["currency"])
        gkey = (str(e.project_id) if e.project_id else None, str(e.employee_id))
        g = groups.setdefault(gkey, {
            "project_id": (str(e.project_id) if e.project_id else None),
            "employee_id": str(e.employee_id),
            "employee_name": _name(db, e.employee_id),
            "rate": rate_info["rate"], "rate_source": rate_info["source"],
            "hours": 0.0, "entry_ids": [], "descriptions": [],
        })
        g["hours"] += float(e.hours or 0)
        g["entry_ids"].append(str(e.id))
        if e.description:
            g["descriptions"].append(e.description)

    if len(currencies) > 1:
        raise TimeBillingError(
            "This client's unbilled time spans multiple currencies "
            f"({', '.join(sorted(currencies))}). Invoice each currency separately."
        )
    currency = (next(iter(currencies)) if currencies else company_currency(db))

    # Order groups by project name, then employee.
    proj_names: dict = {}

    def _proj_name(pid):
        if pid is None:
            return None
        if pid not in proj_names:
            p = db.get(Project, UUID(pid))
            proj_names[pid] = p.name if p else "—"
        return proj_names[pid]

    by_project: dict = {}
    for (pid, _eid), g in groups.items():
        amount = _round(g["hours"] * g["rate"])
        line = {
            "employee_id": g["employee_id"], "employee_name": g["employee_name"],
            "hours": round(g["hours"], 2), "rate": g["rate"], "amount": amount,
            "rate_source": g["rate_source"], "entry_ids": g["entry_ids"],
            "description": "; ".join(dict.fromkeys(g["descriptions"]))[:240] or None,
        }
        bp = by_project.setdefault(pid, {
            "project_id": pid, "project_name": _proj_name(pid) or "General",
            "lines": [], "subtotal": 0,
        })
        bp["lines"].append(line)
        bp["subtotal"] += amount

    groups_out = sorted(by_project.values(), key=lambda b: (b["project_name"] or "").lower())
    for b in groups_out:
        b["lines"].sort(key=lambda ln: ln["employee_name"].lower())

    manual = []
    for m in (manual_lines or []):
        amt = int(m.get("amount") or 0)
        if amt:
            manual.append({"description": (m.get("description") or "Manual line"), "amount": amt})

    subtotal = sum(b["subtotal"] for b in groups_out) + sum(m["amount"] for m in manual)
    tax = 0
    if vat_rate > 0:
        for b in groups_out:
            for ln in b["lines"]:
                tax += _round(ln["amount"] * vat_rate / 100.0)
        for m in manual:
            tax += _round(m["amount"] * vat_rate / 100.0)
    total = subtotal + tax

    dates = [e.work_date for e in entries]
    entry_count = len(entries)
    total_hours = round(sum(float(e.hours or 0) for e in entries), 2)
    terms_days = 30

    return {
        "client_id": str(client_id), "client_name": _name(db, client_id),
        "currency": currency,
        "groups": groups_out, "manual_lines": manual,
        "subtotal": subtotal, "tax": tax, "total": total,
        "tax_code": vat_code, "tax_rate": vat_rate,
        "period_from": (min(dates).isoformat() if dates else None),
        "period_to": (max(dates).isoformat() if dates else None),
        "invoice_date": today.isoformat(),
        "due_date": (due_date.isoformat() if due_date else (today + timedelta(days=terms_days)).isoformat()),
        "entry_count": entry_count, "total_hours": total_hours,
        "empty": entry_count == 0 and not manual,
    }


# ---------------------------------------------------------------------------
# Create the AR invoice from time
# ---------------------------------------------------------------------------


def _next_time_invoice_number(db: Session) -> str:
    from sqlalchemy import func
    n = db.execute(select(func.count(Invoice.id)).where(Invoice.number.like("TINV-%"))).scalar() or 0
    return f"TINV-{n + 1:05d}"


def create_invoice_from_time(db: Session, *, client_id, project_id=None, date_from=None,
                             date_to=None, include_entry_ids=None, invoice_date=None,
                             due_date=None, manual_lines=None, number=None,
                             created_by=None) -> tuple[Invoice, dict]:
    """Build the invoice from unbilled time, post it via the existing AR engine,
    and stamp the contributing entries as invoiced. Returns (Invoice, preview)."""
    preview = build_preview(
        db, client_id=client_id, project_id=project_id, date_from=date_from,
        date_to=date_to, include_entry_ids=include_entry_ids, invoice_date=invoice_date,
        due_date=due_date, manual_lines=manual_lines,
    )
    if preview["empty"]:
        raise TimeBillingError("There is no unbilled time to invoice for this selection.")

    from app.api.invoices import create_invoice as _create_invoice
    from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate

    vat_code = preview["tax_code"]
    treatment = "standard" if vat_code else "exempt"
    items: list[InvoiceItemCreate] = []
    entry_ids: list[str] = []
    for b in preview["groups"]:
        pname = b["project_name"]
        for ln in b["lines"]:
            label = f"{ln['employee_name']} — {pname}"
            if ln.get("description"):
                label = f"{label}: {ln['description']}"
            items.append(InvoiceItemCreate(
                product_name=label[:256], quantity=float(ln["hours"]),
                unit_price=_round(ln["rate"]), line_total=ln["amount"],
                tax_code=vat_code, tax_treatment=treatment,
            ))
            entry_ids.extend(ln["entry_ids"])
    for m in preview["manual_lines"]:
        items.append(InvoiceItemCreate(
            product_name=m["description"][:256], quantity=1, unit_price=m["amount"],
            line_total=m["amount"], tax_code=vat_code, tax_treatment=treatment,
        ))

    inv_read = _create_invoice(InvoiceCreate(
        number=(number or _next_time_invoice_number(db)),
        kind="sales", status="issued",
        issue_date=date.fromisoformat(preview["invoice_date"]),
        due_date=date.fromisoformat(preview["due_date"]),
        amount=0, currency=preview["currency"],
        description=f"Time billing — {preview['client_name']} ({preview['period_from']} → {preview['period_to']})",
        entity_id=client_id, items=items,
    ), db)

    inv = db.get(Invoice, inv_read.id)
    # Stamp the contributing entries as invoiced (locked) with their rate.
    rate_by_entry: dict[str, float] = {}
    for b in preview["groups"]:
        for ln in b["lines"]:
            for eid in ln["entry_ids"]:
                rate_by_entry[eid] = ln["rate"]
    for eid in set(entry_ids):
        e = db.get(TimeEntry, UUID(eid))
        if e is not None and e.status == "unbilled":
            e.status = "invoiced"
            e.invoice_id = inv.id
            e.rate_snapshot = rate_by_entry.get(eid)
            e.currency = preview["currency"]
    db.commit()
    db.refresh(inv)
    return inv, preview


def unbill_for_invoice(db: Session, invoice_id) -> int:
    """Revert every time entry billed on this invoice back to unbilled (used
    when the invoice is voided). Returns the count reverted."""
    entries = db.execute(
        select(TimeEntry).where(TimeEntry.invoice_id == invoice_id)
    ).scalars().all()
    n = 0
    for e in entries:
        e.status = "unbilled"
        e.invoice_id = None
        e.rate_snapshot = None
        n += 1
    if n:
        db.flush()
    return n


# ---------------------------------------------------------------------------
# Payroll dimension — hours-derived pay (Part A)
# ---------------------------------------------------------------------------

# entry_type → payroll behaviour (fixed MVP mapping; configurable in phase 2):
#   work, travel → worked + payable (overtime-eligible)
#   leave        → payable, NOT worked (no overtime)
#   unpaid       → neither
ENTRY_TYPES = ("work", "leave", "travel", "unpaid")
WORKED_TYPES = ("work", "travel")


def default_payable(entry_type: str) -> bool:
    return (entry_type or "work") != "unpaid"


def payroll_hours_summary(
    db: Session, entity_id, period_start: date, period_end: date,
    *, run_id=None,
) -> dict:
    """Sum one worker's PAYABLE hours in a period, split into worked vs leave.

    Only entries not yet settled in another pay run count (``payroll_run_id``
    is null, or equals ``run_id`` when re-reading an existing run's set) — so
    an hour is paid exactly once even across overlapping runs.
    """
    q = select(TimeEntry).where(
        TimeEntry.employee_id == entity_id,
        TimeEntry.work_date >= period_start,
        TimeEntry.work_date <= period_end,
        TimeEntry.payable.is_(True),
    )
    if run_id is None:
        q = q.where(TimeEntry.payroll_run_id.is_(None))
    else:
        q = q.where(TimeEntry.payroll_run_id == run_id)
    entries = db.execute(q).scalars().all()
    worked = sum(float(e.hours or 0) for e in entries if e.entry_type in WORKED_TYPES)
    leave = sum(float(e.hours or 0) for e in entries if e.entry_type == "leave")
    return {
        "worked_hours": round(worked, 2),
        "leave_hours": round(leave, 2),
        "entry_ids": [e.id for e in entries],
        "entry_count": len(entries),
    }
