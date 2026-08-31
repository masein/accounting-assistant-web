"""Materialize due recurring rules into real journal entries.

A rule auto-posts when it is active, ``auto_post`` is on, and it carries an
amount + bank account + counter account. For each due occurrence (``next_run_date``
<= today, within ``end_date``):

    payment: DR counter_account / CR bank_account
    receipt: DR bank_account / CR counter_account

Idempotent by reference — every occurrence posts with reference
``REC-{prefix}-{date}``; if a live transaction with that reference exists the
occurrence is skipped (safe to call from several places: the recurring page
load, the explicit Run-due endpoint, and the external daily scheduler).
Catch-up posts each missed period, capped to avoid runaway backfill.

There is no in-process scheduler in this app (by design — deterministic
startup); materialization is triggered on access and by the same external
cron that hits /notifications/daily-digest.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.recurring import RecurringRule
from app.models.transaction import Transaction

MAX_CATCHUP_OCCURRENCES = 12  # per rule per run


def _next_date(d: date, frequency: str) -> date:
    freq = (frequency or "monthly").lower()
    if freq == "daily":
        return d + timedelta(days=1)
    if freq == "weekly":
        return d + timedelta(weeks=1)
    if freq == "quarterly":
        return _add_months(d, 3)
    if freq == "yearly":
        return _add_months(d, 12)
    return _add_months(d, 1)  # monthly default


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def rule_reference(rule: RecurringRule, run_date: date) -> str:
    prefix = (rule.reference_prefix or "").strip() or f"REC-{str(rule.id)[:8]}"
    if not prefix.upper().startswith("REC"):
        prefix = f"REC-{prefix}"
    return f"{prefix}-{run_date.isoformat()}"[:128]


def _can_auto_post(rule: RecurringRule) -> bool:
    return bool(
        rule.auto_post
        and rule.amount
        and rule.amount > 0
        and (rule.bank_account_code or "").strip()
        and (rule.counter_account_code or "").strip()
    )


def materialize_due_rules(db: Session, *, today: date | None = None) -> dict:
    """Post every due occurrence of every active auto-post rule. Advances
    schedules for ALL active rules (auto-post or reminder-only) so
    ``next_run_date`` always points at the next upcoming occurrence.
    Commits per rule. Returns a summary dict."""
    from fastapi import HTTPException

    from app.services.ledger_posting import create_transaction_from_payload as _create_transaction_from_payload
    from app.schemas.transaction import TransactionCreate

    today = today or date.today()
    rules = db.execute(
        select(RecurringRule).where(
            RecurringRule.status == "active",
            RecurringRule.next_run_date <= today,
        )
    ).scalars().all()

    posted: list[dict] = []
    skipped: list[dict] = []
    for rule in rules:
        occurrences = 0
        while rule.next_run_date <= today and occurrences < MAX_CATCHUP_OCCURRENCES:
            run_date = rule.next_run_date
            if rule.end_date and run_date > rule.end_date:
                rule.status = "paused"  # schedule exhausted
                break
            if _can_auto_post(rule):
                ref = rule_reference(rule, run_date)
                exists = db.execute(
                    select(Transaction.id).where(
                        Transaction.reference == ref,
                        Transaction.deleted_at.is_(None),
                    )
                ).first()
                if not exists:
                    bank = rule.bank_account_code.strip()
                    counter = rule.counter_account_code.strip()
                    if rule.direction == "receipt":
                        lines = [
                            {"account_code": bank, "debit": rule.amount, "credit": 0,
                             "line_description": rule.name},
                            {"account_code": counter, "debit": 0, "credit": rule.amount,
                             "line_description": rule.name},
                        ]
                    else:
                        lines = [
                            {"account_code": counter, "debit": rule.amount, "credit": 0,
                             "line_description": rule.name},
                            {"account_code": bank, "debit": 0, "credit": rule.amount,
                             "line_description": rule.name},
                        ]
                    payload = TransactionCreate(
                        date=run_date,
                        reference=ref,
                        description=f"{rule.name} (recurring)",
                        currency="IRR",
                        lines=lines,
                        entity_links=(
                            [{"entity_id": rule.entity_id,
                              "role": "client" if rule.direction == "receipt" else "supplier"}]
                            if rule.entity_id else []
                        ),
                    )
                    try:
                        txn = _create_transaction_from_payload(db, payload)
                        db.flush()
                        posted.append({"rule_id": str(rule.id), "name": rule.name,
                                       "date": run_date.isoformat(), "reference": ref,
                                       "transaction_id": str(txn.id), "amount": rule.amount})
                    except HTTPException as exc:
                        # closed period / bad account — surface, don't wedge the loop
                        skipped.append({"rule_id": str(rule.id), "name": rule.name,
                                        "date": run_date.isoformat(), "reason": str(exc.detail)})
                        db.rollback()
                        break
                rule.last_run_date = run_date
            rule.next_run_date = _next_date(run_date, rule.frequency)
            occurrences += 1
        db.commit()

    return {"posted": posted, "skipped": skipped,
            "rules_checked": len(rules), "as_of": today.isoformat()}
