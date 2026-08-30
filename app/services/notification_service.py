"""Persisted notification feed: computed from live data, deduped, per-tenant.

``refresh_notifications`` upserts one row per open alert (keyed by
``dedupe_key``) and resolves rows whose source condition cleared. Sources:

- invoices: due within the lead window / overdue (sales AR + purchase AP)
- payroll: pay runs whose ``pay_date`` is near/past and not yet paid
- approvals: pending mileage claims + pending petty-cash expenses
- recurring: reminder-only rules coming due (auto-post rules post instead)
- reminders: user-created reminders inside their lead window (repeat-aware)

Visibility at read time: rows with ``user_id`` belong to that user; NULL rows
are role-gated by ``kind`` (approvals → approver roles; books alerts → books
roles). No delivery here — the existing /notifications/check channels handle
push; this feed backs the in-app bell.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.mileage_claim import MileageClaim
from app.models.notification import Notification, Reminder
from app.models.pay_run import PayRun
from app.models.petty_cash import PettyCashTransaction
from app.models.recurring import RecurringRule

DUE_SOON_DAYS = 3

# kind → roles that see company-wide (user_id IS NULL) rows of that kind
KIND_ROLES = {
    "invoice_due": ("owner", "cfo", "accountant"),
    "invoice_overdue": ("owner", "cfo", "accountant"),
    "payroll": ("owner", "cfo", "accountant"),
    "approvals": ("owner", "cfo", "manager"),
    "petty_cash": ("owner", "cfo", "accountant"),
    "recurring": ("owner", "cfo", "accountant", "personal"),
    "budget": ("owner", "cfo", "accountant", "personal"),
    "commitment": ("owner", "cfo", "accountant", "personal"),
    "reminder": (),  # always personal
}


def _upsert(db: Session, seen: set[str], *, dedupe_key: str, kind: str, level: str,
            title: str, message: str, link_page: str | None = None,
            due_date: date | None = None, user_id: str | None = None) -> None:
    seen.add(dedupe_key)
    row = db.execute(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    ).scalars().first()
    if row is None:
        db.add(Notification(
            dedupe_key=dedupe_key, kind=kind, level=level, title=title,
            message=message, link_page=link_page, due_date=due_date, user_id=user_id,
        ))
    else:
        # refresh content; a previously dismissed row stays dismissed
        row.level = level
        row.title = title
        row.message = message
        row.due_date = due_date
        row.link_page = link_page


def refresh_notifications(db: Session, *, today: date | None = None) -> int:
    """Recompute the feed. Returns the number of open notifications."""
    today = today or date.today()
    soon = today + timedelta(days=DUE_SOON_DAYS)
    seen: set[str] = set()

    # --- invoices: due soon / overdue -------------------------------------
    invoices = db.execute(
        select(Invoice).where(Invoice.status == "issued", Invoice.due_date.is_not(None))
    ).scalars().all()
    for inv in invoices:
        label = "دریافت از مشتری / receivable" if inv.kind == "sales" else "پرداخت به تأمین‌کننده / payable"
        number = inv.number or str(inv.id)[:8]
        if inv.due_date < today:
            days = (today - inv.due_date).days
            _upsert(db, seen, dedupe_key=f"inv-{inv.id}-overdue", kind="invoice_overdue",
                    level="high", title=f"Invoice {number} overdue",
                    message=f"{label} — {days} day(s) past due ({inv.due_date.isoformat()})",
                    link_page="invoices", due_date=inv.due_date)
        elif inv.due_date <= soon:
            _upsert(db, seen, dedupe_key=f"inv-{inv.id}-due", kind="invoice_due",
                    level="warning", title=f"Invoice {number} due {inv.due_date.isoformat()}",
                    message=label, link_page="invoices", due_date=inv.due_date)

    # --- payroll paydays ---------------------------------------------------
    runs = db.execute(
        select(PayRun).where(PayRun.status != "paid", PayRun.pay_date.is_not(None))
    ).scalars().all()
    for run in runs:
        if run.pay_date <= soon:
            level = "high" if run.pay_date < today else "warning"
            _upsert(db, seen, dedupe_key=f"payrun-{run.id}", kind="payroll", level=level,
                    title=f"Payroll payday {run.pay_date.isoformat()}",
                    message=f"Pay run {run.period_start}–{run.period_end} is {run.status} — pay date "
                            + ("passed" if run.pay_date < today else "coming up"),
                    link_page="payroll", due_date=run.pay_date)

    # --- pending approvals -------------------------------------------------
    pending_claims = db.execute(
        select(MileageClaim).where(MileageClaim.status == "pending_approval")
    ).scalars().all()
    if pending_claims:
        _upsert(db, seen, dedupe_key="expenses-pending", kind="approvals", level="warning",
                title=f"{len(pending_claims)} expense claim(s) awaiting approval",
                message="Review and approve or reject the pending expense claims.",
                link_page="expenses")

    pending_petty = db.execute(
        select(PettyCashTransaction).where(
            PettyCashTransaction.status == "pending",
            PettyCashTransaction.kind == "expense",
        )
    ).scalars().all()
    if pending_petty:
        _upsert(db, seen, dedupe_key="petty-pending", kind="petty_cash", level="warning",
                title=f"{len(pending_petty)} petty cash expense(s) awaiting approval",
                message="Review the pending تنخواه expenses.", link_page="petty-cash")

    # --- reminder-only recurring rules coming due --------------------------
    rules = db.execute(
        select(RecurringRule).where(
            RecurringRule.status == "active",
            RecurringRule.next_run_date <= soon,
        )
    ).scalars().all()
    for rule in rules:
        if rule.auto_post and rule.amount and rule.bank_account_code and rule.counter_account_code:
            continue  # posts itself; no reminder needed
        _upsert(db, seen, dedupe_key=f"rec-{rule.id}-{rule.next_run_date.isoformat()}",
                kind="recurring", level="info",
                title=f"Recurring: {rule.name} due {rule.next_run_date.isoformat()}",
                message=(f"{rule.direction} of {rule.amount:,}" if rule.amount else rule.direction),
                link_page="recurring", due_date=rule.next_run_date)

    # --- user reminders ----------------------------------------------------
    reminders = db.execute(
        select(Reminder).where(Reminder.status == "active")
    ).scalars().all()
    for rem in reminders:
        # a repeating reminder whose date has passed rolls to its next occurrence
        while rem.repeat != "none" and rem.due_date < today:
            rem.due_date = _advance(rem.due_date, rem.repeat)
        if rem.repeat == "none" and rem.due_date < today - timedelta(days=7):
            rem.status = "done"  # stale one-shot, auto-retire after a week
            continue
        if rem.due_date - timedelta(days=max(rem.days_before, 0)) <= today:
            level = "high" if rem.due_date < today else "warning"
            _upsert(db, seen, dedupe_key=f"rem-{rem.id}-{rem.due_date.isoformat()}",
                    kind="reminder", level=level,
                    title=rem.title,
                    message=(rem.note or "") + f" — due {rem.due_date.isoformat()}",
                    due_date=rem.due_date, user_id=rem.user_id)

    # --- installments & cheques falling due -------------------------------
    # The reason this feature exists: a missed قسط or an uncovered cheque has
    # consequences well beyond the bookkeeping, so warn before the date, not
    # after. Overdue stays on the feed until it is settled or written off.
    try:
        from app.models.commitment import BOUNCED, CHEQUE, PENDING, Commitment

        due_rows = db.execute(
            select(Commitment).where(Commitment.status.in_([PENDING, BOUNCED]))
        ).scalars().all()
        for c in due_rows:
            overdue = c.due_date < today
            if not overdue and c.due_date > soon:
                continue
            if c.status == BOUNCED:
                level, when = "high", f"bounced — still outstanding ({c.due_date.isoformat()})"
            elif overdue:
                level, when = "high", f"{(today - c.due_date).days} day(s) overdue ({c.due_date.isoformat()})"
            else:
                level, when = "warning", f"due {c.due_date.isoformat()}"
            noun = "Cheque" if c.kind == CHEQUE else "Installment"
            seq = f" {c.sequence}/{c.plan_total}" if c.sequence and c.plan_total else ""
            verb = "to pay" if c.direction == "pay" else "to receive"
            _upsert(db, seen, dedupe_key=f"commitment-{c.id}", kind="commitment",
                    level=level, title=f"{noun}{seq}: {c.title}",
                    message=f"{c.amount:,} {verb} — {when}",
                    link_page="commitments", due_date=c.due_date)
    except Exception:
        # Never let this break the whole feed refresh.
        pass

    # --- budgets: current month at >=85% (warning) / >=100% (over, high) ---
    month = f"{today.year:04d}-{today.month:02d}"
    try:
        from app.services.budget_service import budget_utilization

        for row in budget_utilization(db, month):
            pct = row["utilization_pct"]
            if pct < 85:
                continue
            over = pct >= 100
            _upsert(db, seen, dedupe_key=f"budget-{month}-{row['category']}",
                    kind="budget", level="high" if over else "warning",
                    title=(f"Budget exceeded: {row['category']}" if over
                           else f"Budget at {int(pct)}%: {row['category']}"),
                    message=(f"{row['actual_amount']:,} of {row['limit_amount']:,} "
                             f"spent in {month} ({row['utilization_pct']}%)"),
                    link_page="personal-dashboard")
    except Exception:
        # budget alerts must never break the whole feed refresh
        pass

    # --- resolve rows whose source condition cleared -----------------------
    open_rows = db.execute(
        select(Notification).where(Notification.dismissed_at.is_(None))
    ).scalars().all()
    open_count = 0
    now = datetime.now(timezone.utc)
    for row in open_rows:
        if row.dedupe_key not in seen:
            row.dismissed_at = now  # condition cleared (paid, approved, done)
        else:
            open_count += 1
    db.commit()
    return open_count


def _advance(d: date, repeat: str) -> date:
    from app.services.recurring_service import _add_months

    r = (repeat or "none").lower()
    if r == "daily":
        return d + timedelta(days=1)
    if r == "weekly":
        return d + timedelta(weeks=1)
    if r == "yearly":
        return _add_months(d, 12)
    return _add_months(d, 1)  # monthly


def visible_to(row: Notification, *, user_id: str, role: str) -> bool:
    if row.user_id:
        return row.user_id == user_id
    roles = KIND_ROLES.get(row.kind, ("owner",))
    return role in roles or role == "owner"
