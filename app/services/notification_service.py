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
    "moadian": ("owner", "cfo", "accountant"),
    "api_key": ("owner",),
    "ai_budget": ("owner", "cfo"),
    "tax_filing": ("owner", "cfo", "accountant"),
    "payroll": ("owner", "cfo", "accountant"),
    "approvals": ("owner", "cfo", "manager"),
    "petty_cash": ("owner", "cfo", "accountant"),
    "recurring": ("owner", "cfo", "accountant", "personal"),
    "budget": ("owner", "cfo", "accountant", "personal"),
    "commitment": ("owner", "cfo", "accountant", "personal"),
    "insight": ("owner", "cfo", "accountant", "personal"),
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


API_KEY_WARN_DAYS = 14


def _api_key_expiry(db: Session, seen: set[str], today: date) -> None:
    """Warn the owner two weeks before an integration key expires (and once
    it has): an expired key silently stops a Jira/Toggl sync."""
    from app.db.tenant import get_current_company
    from app.models.api_key import ApiKey
    cid = get_current_company()
    if not cid:
        return
    import uuid as _uuid
    try:
        company_uuid = _uuid.UUID(str(cid))
    except ValueError:
        return
    horizon = datetime.combine(today + timedelta(days=API_KEY_WARN_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    keys = db.execute(select(ApiKey).where(
        ApiKey.company_id == company_uuid, ApiKey.revoked.is_(False),
        ApiKey.expires_at.is_not(None), ApiKey.expires_at <= horizon,
    )).scalars().all()
    for k in keys:
        exp = k.expires_at if k.expires_at.tzinfo else k.expires_at.replace(tzinfo=timezone.utc)
        left = (exp.date() - today).days
        _upsert(db, seen, dedupe_key=f"apikey-{k.id}", kind="api_key",
                level="high" if left < 0 else "warning",
                title=(f"API key '{k.label}' has expired" if left < 0 else f"API key '{k.label}' expires in {left} day(s)"),
                message=f"Integrations using {k.prefix}… stop working on {exp.date().isoformat()}. "
                        "Create a new key under Settings → API keys and update the integration.",
                link_page="settings", due_date=exp.date())


UK_VAT_WARN_DAYS = 10


def _uk_vat_deadline(db: Session, seen: set[str], today: date) -> None:
    """UK VAT-registered companies (MTD settings): the return for the period
    that last ended is due one month and seven days later
    (app/services/uk_mtd). Remind ten days before, louder in the last two."""
    try:
        from app.services.fx_service import _current_company_row
        row = _current_company_row(db)
    except Exception:
        row = None
    if row is None or (getattr(row, "locale", "") or "").lower() != "uk":
        return
    from app.services.uk_mtd import periods as P
    from app.services.uk_mtd import settings as S
    conf = S.get_settings(db)
    if not conf.get("vat_registered"):
        return
    for period in P.vat_periods_before(today, conf["vat_stagger"], count=3):
        if period.end >= today:
            continue  # still open
        left = (period.deadline - today).days
        if 0 <= left <= UK_VAT_WARN_DAYS:
            _upsert(db, seen, dedupe_key=f"uk-vat-{period.key}", kind="tax_filing",
                    level="high" if left <= 2 else "warning",
                    title=f"VAT return for {period.start.strftime('%b')}–{period.end.strftime('%b %Y')} due "
                          + ("today" if left == 0 else f"in {left} day(s)"),
                    message="Boxes 1–9 and a CSV for your MTD software: Invoices → Making Tax Digital.",
                    link_page="invoices", due_date=period.deadline)


TTMS_WARN_DAYS = 10
VAT_RETURN_WARN_DAYS = 7


def _tax_filing_deadlines(db: Session, seen: set[str], today: date) -> None:
    """Iranian companies: the season that just ended has a VAT return due 15
    days later and a TTMS report 45 days later (app/services/tax_ir.py).
    Remind before each, louder in the last two days; only when the season
    had any invoices."""
    try:
        from app.services.fx_service import _current_company_row
        row = _current_company_row(db)
    except Exception:
        row = None
    if row is None or (getattr(row, "locale", "") or "").lower() != "ir":
        return
    from app.services import tax_ir
    season = tax_ir.season_of(today).previous()
    if not tax_ir._season_invoices(db, season):
        return
    for kind, deadline, warn, title in (
        ("vat", season.vat_deadline, VAT_RETURN_WARN_DAYS, f"VAT return for {season.name} due"),
        ("ttms", season.ttms_deadline, TTMS_WARN_DAYS, f"Quarterly transactions report (TTMS) for {season.name} due"),
    ):
        left = (deadline - today).days
        if 0 <= left <= warn:
            _upsert(db, seen, dedupe_key=f"tax-{kind}-{season.year}-{season.season}", kind="tax_filing",
                    level="high" if left <= 2 else "warning",
                    title=f"{title} {'today' if left == 0 else f'in {left} day(s)'}",
                    message="Figures and the Excel file: Invoices → Seasonal tax reports. "
                            "A Friday or holiday deadline moves to the next working day.",
                    link_page="invoices", due_date=deadline)


AI_BUDGET_WARN_SHARE = 0.8


def _ai_budget(db: Session, seen: set[str], today: date) -> None:
    """Warn the owner when the company has used 80 % of its 24-hour AI
    allowance (app/services/ai_usage.py), and louder once it is used up —
    before the chat starts refusing."""
    from app.db.tenant import get_current_company
    from app.services import ai_usage
    cid = ai_usage._uuid(get_current_company())
    if cid is None:
        return
    limits = ai_usage.load_settings(db)
    budget, _user_budget = ai_usage.effective_budgets(db, cid, limits)
    if not budget:
        return
    used, _oldest = ai_usage._used_since(db, ai_usage._now() - ai_usage.WINDOW, company_id=cid)
    if used < budget * AI_BUDGET_WARN_SHARE:
        return
    pct = int(used * 100 / budget)
    _upsert(db, seen, dedupe_key=f"ai-budget-{today.isoformat()}", kind="ai_budget",
            level="high" if used >= budget else "warning",
            title=(f"AI allowance used up ({pct}%)" if used >= budget else f"AI allowance {pct}% used"),
            message=f"{used:,} of {budget:,} tokens in the last 24 hours. "
                    + ("AI features are paused until older use drops out of the window."
                       if used >= budget else "AI features pause when it reaches 100%.")
                    + " See Settings → AI usage.",
            link_page="settings")


def _moadian_deadlines(db: Session, seen: set[str], today: date) -> None:
    """Only for companies that use مودیان (memory id entered): a warning
    three days before the sending deadline, high once it has passed."""
    from app.services.moadian.settings import WARN_DAYS_BEFORE, enabled, get_settings
    if not enabled(db):
        return
    days = int(get_settings(db)["deadline_days"])
    horizon = today - timedelta(days=days - WARN_DAYS_BEFORE)
    rows = db.execute(select(Invoice).where(
        Invoice.kind == "sales", Invoice.status.in_(("issued", "partially_paid", "paid")),
        Invoice.moadian_status.is_(None), Invoice.issue_date <= horizon,
    )).scalars().all()
    for inv in rows:
        due = inv.issue_date + timedelta(days=days)
        left = (due - today).days
        number = inv.number or str(inv.id)[:8]
        if left < 0:
            _upsert(db, seen, dedupe_key=f"moadian-{inv.id}", kind="moadian", level="high",
                    title=f"Invoice {number} not sent to سامانه مودیان",
                    message=f"The {days}-day deadline passed on {due.isoformat()} ({-left} day(s) ago).",
                    link_page="invoices", due_date=due)
        else:
            _upsert(db, seen, dedupe_key=f"moadian-{inv.id}", kind="moadian", level="warning",
                    title=f"Send invoice {number} to سامانه مودیان",
                    message=f"Deadline {due.isoformat()} ({left} day(s) left).",
                    link_page="invoices", due_date=due)


def _budget_link_page(db: Session) -> str:
    """Budgets live on the personal dashboard for personal tenants; a business
    owner has no such page (clicking bounced them home — QA 2026-09-24 3.26), so
    they land on the main dashboard instead."""
    try:
        from app.services.fx_service import _current_company_row
        row = _current_company_row(db)
        if row is not None and (getattr(row, "kind", None) or "business") == "personal":
            return "personal-dashboard"
    except Exception:
        pass
    return "dashboard"


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

    # --- سامانه مودیان: 12-day sending deadline -------------------------------
    _moadian_deadlines(db, seen, today)

    # --- integration keys about to stop working --------------------------------
    _api_key_expiry(db, seen, today)

    # --- AI allowance nearly used ------------------------------------------------
    _ai_budget(db, seen, today)

    # --- Iranian seasonal filings (VAT return, TTMS) ------------------------------
    _tax_filing_deadlines(db, seen, today)

    # --- UK MTD VAT return ----------------------------------------------------------
    _uk_vat_deadline(db, seen, today)

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
                    link_page=_budget_link_page(db))
    except Exception:
        # budget alerts must never break the whole feed refresh
        pass

    # --- proactive insights: payroll moves, expense spikes, statement due … --
    # Computed by insight_service (cached ~10 min per tenant, so the 90-second
    # bell poll doesn't rescan a year of ledger each time). Keys carry the
    # period, so an insight re-emits while its condition holds and auto-resolves
    # below once it no longer does.
    try:
        from app.services.insight_service import compute_insights, insight_language

        lang = insight_language(db)
        for ins in compute_insights(db, today=today):
            loc = ins.localize(lang)
            _upsert(db, seen, dedupe_key=f"insight-{ins.key}"[:160], kind="insight",
                    level=ins.severity if ins.severity in ("info", "warning", "high") else "info",
                    title=loc["title"][:256], message=loc["message"], link_page=ins.page)
    except Exception:
        # insights must never break the whole feed refresh
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
