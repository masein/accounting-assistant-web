"""Period lock (book close).

A single setting, ``closed_period``, holds the date the books are locked
*through* (inclusive). Any journal entry dated on or before that date is in a
closed period and must not be posted or back-dated into. Enforced at the
transaction API, the invoice/payment/credit-note postings, and the AI
accountant's date guard.

Stored in AppSetting under ``closed_period`` as an ISO date string (empty =
no lock).
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

CLOSED_PERIOD_KEY = "closed_period"


def get_closed_period(db: Session) -> date | None:
    """The date the books are locked through (inclusive), or None if open."""
    row = db.execute(
        select(AppSetting).where(AppSetting.key == CLOSED_PERIOD_KEY)
    ).scalar_one_or_none()
    if not row or not (row.value or "").strip():
        return None
    try:
        return date.fromisoformat(row.value.strip())
    except ValueError:
        return None


def set_closed_period(db: Session, value: date | None) -> date | None:
    """Lock the books through ``value`` (inclusive), or clear the lock with
    None."""
    iso = value.isoformat() if value else ""
    row = db.execute(
        select(AppSetting).where(AppSetting.key == CLOSED_PERIOD_KEY)
    ).scalar_one_or_none()
    if row:
        row.value = iso
    else:
        db.add(AppSetting(key=CLOSED_PERIOD_KEY, value=iso))
    db.flush()
    return value


def is_period_locked(db: Session, entry_date: date) -> bool:
    locked_through = get_closed_period(db)
    return locked_through is not None and entry_date <= locked_through


def assert_period_open(db: Session, entry_date: date) -> None:
    """Raise HTTP 422 if ``entry_date`` falls in a locked (closed) period."""
    locked_through = get_closed_period(db)
    if locked_through is not None and entry_date <= locked_through:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Period is closed through {locked_through.isoformat()}; cannot post or "
                f"back-date an entry dated {entry_date.isoformat()}. Reopen the period "
                f"or use a later date."
            ),
        )
