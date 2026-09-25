"""Recurring sales invoices: schedule maths and the daily generator.

``generate_due`` issues every occurrence that has come due (catching up at
most ``MAX_CATCHUP`` per template per run), one commit per invoice, through
the same ``insert_invoice`` path as a hand-made invoice — same numbering,
tax resolution, AR recognition and closed-period guard. A failure (e.g. the
period is closed) is recorded on the template and the schedule is NOT
advanced, so the occurrence is retried the next day rather than skipped.
Auto-send e-mails each issued invoice after it is committed; a mail problem
never undoes the invoice.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.recurring_invoice import RecurringInvoice

FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly")
CALENDARS = ("gregorian", "jalali")
MAX_CATCHUP = 12
_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}


def _gregorian_add_months(d: date, months: int) -> date:
    idx = d.month - 1 + months
    y, m = d.year + idx // 12, idx % 12 + 1
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _jalali_month_len(y: int, m: int) -> int:
    import jdatetime
    if m <= 6:
        return 31
    if m <= 11:
        return 30
    return 30 if jdatetime.date(y, 1, 1).isleap() else 29


def _jalali_add_months(d: date, months: int) -> date:
    import jdatetime
    j = jdatetime.date.fromgregorian(date=d)
    idx = j.month - 1 + months
    y, m = j.year + idx // 12, idx % 12 + 1
    day = min(j.day, _jalali_month_len(y, m))
    g = jdatetime.date(y, m, day).togregorian()
    return date(g.year, g.month, g.day)


def occurrence_date(start: date, frequency: str, calendar: str, n: int) -> date:
    """Date of occurrence ``n`` (0 = the start). Anchored on the start so a
    schedule that began on the 31st stays on the month's last day instead of
    drifting to the 28th."""
    freq = (frequency or "monthly").lower()
    if freq == "weekly":
        return start + timedelta(weeks=n)
    months = _MONTHS.get(freq, 1) * n
    if (calendar or "gregorian").lower() == "jalali":
        return _jalali_add_months(start, months)
    return _gregorian_add_months(start, months)


def template_items(t: RecurringInvoice) -> list[dict]:
    try:
        data = json.loads(t.items or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def template_total(db: Session, t: RecurringInvoice, on: date) -> int:
    """What one occurrence will bill (tax-inclusive), for the list view."""
    from app.api.invoices import _build_invoice_items
    from app.schemas.invoice import InvoiceItemCreate
    items = [InvoiceItemCreate(**it) for it in template_items(t)]
    if not items:
        return int(t.amount or 0)
    _rows, total = _build_invoice_items(items, None, db, on)
    return int(total)


def _finished(t: RecurringInvoice, run_date: date) -> bool:
    if t.end_date is not None and run_date > t.end_date:
        return True
    if t.max_occurrences is not None and int(t.occurrences or 0) >= int(t.max_occurrences):
        return True
    return False


def _issue_one(db: Session, t: RecurringInvoice, run_date: date):
    from app.api.invoices import insert_invoice, invoice_number_prefix, suggest_number
    from app.models.invoice import Invoice
    from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate

    inv = insert_invoice(db, InvoiceCreate(
        number=suggest_number(db, Invoice, invoice_number_prefix(db), kind="sales"),
        kind="sales", status=t.issue_status or "issued", issue_date=run_date,
        due_date=run_date + timedelta(days=max(0, int(t.terms_days or 0))),
        amount=int(t.amount or 0), currency=t.currency,
        description=t.description or t.name, entity_id=t.entity_id,
        items=[InvoiceItemCreate(**it) for it in template_items(t)],
    ))
    inv.recurring_invoice_id = t.id
    return inv


def generate_due(db: Session, *, today: date | None = None, template_id=None) -> dict:
    """Issue every due occurrence. Returns counts plus the ids issued."""
    from fastapi import HTTPException

    from app.services.audit_service import log_audit_event
    from app.services.invoice_mail import InvoiceMailError, send_invoice_email

    today = today or date.today()
    q = select(RecurringInvoice).where(RecurringInvoice.status == "active",
                                       RecurringInvoice.next_run_date <= today)
    if template_id is not None:
        q = q.where(RecurringInvoice.id == template_id)
    out = {"issued": 0, "emailed": 0, "failed": 0, "ended": 0, "invoice_ids": []}
    for t in db.execute(q).scalars().all():
        tid = t.id
        done = 0
        to_send = []
        while t.next_run_date <= today and done < MAX_CATCHUP:
            run_date = t.next_run_date
            if _finished(t, run_date):
                t.status = "ended"
                db.commit()
                out["ended"] += 1
                break
            try:
                inv = _issue_one(db, t, run_date)
            except HTTPException as e:
                db.rollback()
                t = db.get(RecurringInvoice, tid)
                t.last_error = f"{run_date.isoformat()}: {e.detail}"[:1000]
                t.last_run_at = datetime.now(timezone.utc)
                db.commit()
                out["failed"] += 1
                break
            t.occurrences = int(t.occurrences or 0) + 1
            t.next_run_date = occurrence_date(t.start_date, t.frequency, t.calendar, t.occurrences)
            t.last_invoice_id = inv.id
            t.last_run_at = datetime.now(timezone.utc)
            t.last_error = None
            log_audit_event(db, action="create", entity_type="invoice", entity_id=str(inv.id),
                            detail=f"Invoice {inv.number} raised by recurring template '{t.name}'")
            db.commit()
            out["issued"] += 1
            out["invoice_ids"].append(str(inv.id))
            done += 1
            if t.auto_send and inv.status != "draft":
                to_send.append(inv.id)
            if _finished(t, t.next_run_date):
                t.status = "ended"
                db.commit()
                out["ended"] += 1
                break
        # E-mail after the invoices are safely committed.
        from app.models.invoice import Invoice
        for inv_id in to_send:
            inv = db.get(Invoice, inv_id)
            try:
                row = send_invoice_email(db, inv, actor="scheduler", today=today)
                db.commit()
                if row.status == "sent":
                    out["emailed"] += 1
                else:
                    t.last_error = f"Invoice {inv.number} not e-mailed: {row.error}"[:1000]
                    db.commit()
            except InvoiceMailError as e:
                db.rollback()
                t = db.get(RecurringInvoice, tid)
                t.last_error = f"Invoice {inv.number} not e-mailed: {e}"[:1000]
                db.commit()
    return out
