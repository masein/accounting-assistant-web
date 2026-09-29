"""Savings goals for personal books (roadmap §4.12).

A goal targets one asset account; its progress is that account's value today
— the market value where gold or currency holdings revalue it, as the net
worth panel shows it. With a target date it also says what each month still
needs (the months left in the company's calendar, this one included) and
whether the last three months' pace keeps up.
"""
from __future__ import annotations

import math
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.savings_goal import SavingsGoal
from app.services.reporting.common import ASSET, classify_account_code

PACE_MONTHS = 3


class GoalError(ValueError):
    pass


def _values(db: Session, on: date) -> dict[str, int]:
    """Each asset account's value on a date — market value where holdings revalue it."""
    from app.services.net_worth_service import compute_net_worth
    nw = compute_net_worth(db, as_of=on, with_trend=False)
    return {ln.account_code: int(ln.market_value) for ln in nw.assets}


def months_left(today: date, target: date, cal: str) -> int:
    """Months from this one to the target's, both included; 0 once the date has passed."""
    from app.services.calendar_periods import months_between
    if target < today:
        return 0
    return len(months_between(today, target, cal))


def check(db: Session, *, name: str, account_code: str, target_amount: int) -> Account:
    if not (name or "").strip():
        raise GoalError("Give the goal a name.")
    if int(target_amount or 0) <= 0:
        raise GoalError("The target must be more than zero.")
    acc = db.execute(select(Account).where(Account.code == (account_code or "").strip())).scalars().first()
    if acc is None:
        raise GoalError(f"No account {account_code}.")
    if classify_account_code(acc.code) != ASSET:
        raise GoalError("A goal saves into an asset account — a savings deposit, gold, a currency account.")
    return acc


def progress(db: Session, goals: list[SavingsGoal], *, today: date | None = None) -> list[dict]:
    from app.services.calendar_periods import company_calendar
    today = today or date.today()
    if not goals:
        return []
    cal = company_calendar(db)
    now = _values(db, today)
    before_day = pace_start(today, cal)
    before = _values(db, before_day)
    names = {a.code: a.name for a in db.execute(
        select(Account).where(Account.code.in_({g.account_code for g in goals}))).scalars()}
    out = []
    for g in goals:
        current = now.get(g.account_code, 0)
        target = int(g.target_amount)
        remaining = max(0, target - current)
        pace = (current - before.get(g.account_code, 0)) / PACE_MONTHS
        left = months_left(today, g.target_date, cal) if g.target_date else None
        needed = None if left is None else (remaining if left == 0 else math.ceil(remaining / left))
        on_track = None if g.target_date is None else (remaining == 0 or (left > 0 and pace >= needed))
        out.append({
            "id": str(g.id), "name": g.name, "account_code": g.account_code, "account_name": names.get(g.account_code),
            "target_amount": target, "current": current, "remaining": remaining,
            "percent": round(min(100.0, current / target * 100), 1) if target else 0.0,
            "reached": current >= target, "target_date": g.target_date.isoformat() if g.target_date else None,
            "months_left": left, "needed_per_month": needed, "pace_per_month": int(round(pace)),
            "on_track": on_track,
            "months_to_go": (math.ceil(remaining / pace) if pace > 0 and remaining else (0 if not remaining else None)),
            "archived": bool(g.archived),
        })
    return out


def pace_start(today: date, cal: str) -> date:
    """The same day three months back in the company's calendar (the month's last day if it's shorter)."""
    from datetime import timedelta

    from app.services.calendar_periods import bounds, month_of, shift
    y, m = month_of(today, cal)
    first_now, _ = bounds(y, m, cal)
    into = (today - first_now).days
    py, pm = shift(y, m, -PACE_MONTHS)
    first, last = bounds(py, pm, cal)
    return min(last, first + timedelta(days=into))


def list_goals(db: Session, *, include_archived: bool = False, today: date | None = None) -> list[dict]:
    q = select(SavingsGoal).order_by(SavingsGoal.archived, SavingsGoal.target_date.is_(None), SavingsGoal.target_date,
                                     SavingsGoal.created_at)
    if not include_archived:
        q = q.where(SavingsGoal.archived.is_(False))
    return progress(db, list(db.execute(q).scalars()), today=today)
