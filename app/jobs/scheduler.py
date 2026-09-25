"""In-process scheduler for the work that used to run only while someone had
a browser open (roadmap 2026-09 §2.1).

Jobs run for every active company inside ``use_company`` with their own DB
session, so tenant scoping and row stamping behave exactly as in a request.
Each job records ``job:<name>`` = last run date in that company's
``app_settings`` row, which makes the daily jobs idempotent across restarts
and across the two-minute Watchtower redeploys. A failure in one company is
logged and never stops the others.

Jobs
----
* ``recurring_run_due``      daily      post due recurring rules (idempotent per occurrence)
* ``notifications_refresh``  every 15 m  rebuild the in-app feed (due invoices, budgets, insights …)
* ``daily_digest``           daily at SCHEDULER_DIGEST_HOUR (server time) deliver the cash digest
                              to the company's configured channel
* ``recurring_invoices``     daily at SCHEDULER_DIGEST_HOUR  issue due recurring sales invoices
                              (and e-mail them when the template says so)
* ``invoice_reminders``      daily at SCHEDULER_DIGEST_HOUR  e-mail overdue customers (only for
                              companies that switched reminders on; see app/services/invoice_mail.py)

Runs in a single asyncio task started from the app lifespan; sync DB work is
pushed to a thread so requests are never blocked. Disable with
SCHEDULER_ENABLED=false (tests never start it: the lifespan is overridden).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select

from app.core.config import settings

log = logging.getLogger("app.jobs")

TICK_SECONDS = 60


@dataclass
class JobStatus:
    last_started: datetime | None = None
    last_finished: datetime | None = None
    last_error: str | None = None
    companies_ok: int = 0
    companies_failed: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_started": self.last_started.isoformat() if self.last_started else None,
            "last_finished": self.last_finished.isoformat() if self.last_finished else None,
            "last_error": self.last_error,
            "companies_ok": self.companies_ok,
            "companies_failed": self.companies_failed,
            "detail": self.detail,
        }


STATUS: dict[str, JobStatus] = {}
_last_refresh_tick: datetime | None = None


def _session_factory():
    from app.db.session import SessionLocal
    return SessionLocal


def _active_company_ids(db) -> list[str]:
    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    with tenant_bypass():
        rows = db.execute(select(Company.id).where(Company.status == "active")).all()
    return [str(r[0]) for r in rows]


def _marker(db, name: str) -> str | None:
    from app.models.app_setting import AppSetting
    row = db.execute(select(AppSetting).where(AppSetting.key == f"job:{name}")).scalars().first()
    return row.value if row else None


def _set_marker(db, name: str, value: str) -> None:
    from app.models.app_setting import AppSetting
    row = db.execute(select(AppSetting).where(AppSetting.key == f"job:{name}")).scalars().first()
    if row is None:
        db.add(AppSetting(key=f"job:{name}", value=value))
    else:
        row.value = value
    db.commit()


# --- the jobs (sync; run in a worker thread) -------------------------------

def job_recurring_run_due(db, today: date) -> dict:
    from app.services.recurring_service import materialize_due_rules
    return materialize_due_rules(db, today=today)


def job_notifications_refresh(db, today: date) -> dict:
    from app.services.notification_service import refresh_notifications
    return {"created": refresh_notifications(db, today=today)}


def job_daily_digest(db, today: date) -> dict:
    """Build + deliver the digest through the same channel helpers the
    /notifications/daily-digest endpoint uses. Skips silently when the
    company has the digest disabled."""
    from app.api.notifications import _company_name, _send_email, _send_slack, _send_telegram
    from app.services.digest_service import build_daily_digest, format_digest

    d = build_daily_digest(db)
    conf = d["settings"]
    if not conf.get("enabled"):
        return {"delivered": [], "enabled": False}
    text = format_digest(_company_name(db), d)
    delivered: list[str] = []
    ch = conf.get("channel")

    async def _push():
        if ch in ("all", "slack") and await _send_slack(text):
            delivered.append("slack")
        if ch in ("all", "telegram") and await _send_telegram(text):
            delivered.append("telegram")

    asyncio.run(_push())
    if ch in ("all", "email"):
        try:
            if _send_email(text):
                delivered.append("email")
        except Exception:  # mail is best-effort; the digest itself succeeded
            log.warning("digest_email_failed", exc_info=True)
    return {"delivered": delivered, "enabled": True}


def job_recurring_invoices(db, today: date) -> dict:
    from app.services.recurring_invoice_service import generate_due
    out = generate_due(db, today=today)
    out.pop("invoice_ids", None)
    return out


def job_invoice_reminders(db, today: date) -> dict:
    from app.services.invoice_mail import run_reminders
    return run_reminders(db, today=today)


def run_job_for_all_companies(name: str, fn: Callable[[Any, date], dict], *, today: date,
                              once_per_day: bool) -> JobStatus:
    """Run ``fn(db, today)`` under every active company. ``once_per_day`` jobs
    skip companies whose ``job:<name>`` marker already equals ``today``."""
    from app.db.tenant import use_company
    factory = _session_factory()
    status = STATUS.setdefault(name, JobStatus())
    status.last_started = datetime.now()
    status.companies_ok = status.companies_failed = 0
    status.detail = {}
    db = factory()
    try:
        company_ids = _active_company_ids(db)
    finally:
        db.close()
    for cid in company_ids:
        db = factory()
        try:
            with use_company(cid):
                if once_per_day and _marker(db, name) == today.isoformat():
                    continue
                result = fn(db, today)
                if once_per_day:
                    _set_marker(db, name, today.isoformat())
            status.companies_ok += 1
            if result:
                status.detail[cid] = result
        except Exception as exc:  # one tenant's problem must not stop the others
            db.rollback()
            status.companies_failed += 1
            status.last_error = f"{cid}: {exc!r}"
            log.exception("job_failed job=%s company=%s", name, cid)
        finally:
            db.close()
    status.last_finished = datetime.now()
    return status


def run_pending_jobs(now: datetime | None = None) -> list[str]:
    """One scheduler tick: decide which jobs are due and run them. Returns the
    names that ran (used by the tests and the admin status view)."""
    global _last_refresh_tick
    now = now or datetime.now()
    today = now.date()
    ran: list[str] = []
    # Recurring rules: once a day per company, as early as the day starts.
    run_job_for_all_companies("recurring_run_due", job_recurring_run_due, today=today, once_per_day=True)
    ran.append("recurring_run_due")
    # Feed refresh: every 15 minutes (cheap; insights are cached server-side).
    if _last_refresh_tick is None or now - _last_refresh_tick >= timedelta(minutes=15):
        run_job_for_all_companies("notifications_refresh", job_notifications_refresh, today=today, once_per_day=False)
        _last_refresh_tick = now
        ran.append("notifications_refresh")
    # Digest: once a day, from the configured hour onwards.
    if now.hour >= int(settings.scheduler_digest_hour):
        run_job_for_all_companies("daily_digest", job_daily_digest, today=today, once_per_day=True)
        ran.append("daily_digest")
        # Recurring invoices first (they may be e-mailed), then reminders — both
        # in business hours, never at midnight.
        run_job_for_all_companies("recurring_invoices", job_recurring_invoices, today=today, once_per_day=True)
        ran.append("recurring_invoices")
        run_job_for_all_companies("invoice_reminders", job_invoice_reminders, today=today, once_per_day=True)
        ran.append("invoice_reminders")
    return ran


async def scheduler_loop(stop: asyncio.Event) -> None:
    log.info("scheduler started (tick=%ss, digest_hour=%s)", TICK_SECONDS, settings.scheduler_digest_hour)
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_pending_jobs)
        except Exception:  # never let the loop die
            log.exception("scheduler_tick_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("scheduler stopped")


def status_snapshot() -> dict[str, Any]:
    return {
        "enabled": bool(settings.scheduler_enabled),
        "tick_seconds": TICK_SECONDS,
        "digest_hour": int(settings.scheduler_digest_hour),
        "jobs": {name: st.as_dict() for name, st in STATUS.items()},
    }
