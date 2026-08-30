"""Budget actuals — shared by /budgets/actual-vs-budget and the
notification feed's budget alerts.

Expense detection is locale-agnostic via ``classify_account_code`` (Iran
5x/6x, UK 5/7/8/9xxx, and the personal chart's 61xx/62xx), replacing the
old hard-coded ``61``/``62`` prefix check that returned zero actuals for
UK-locale charts. The month is filtered in SQL instead of loading every
transaction into Python.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.budget import BudgetLimit
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import EXPENSE, classify_account_code


def month_bounds(month: str) -> tuple[date, date]:
    """'YYYY-MM' → (first day, last day)."""
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, 1), date(year, mon, monthrange(year, mon)[1])


def expense_actuals_by_category(db: Session, month: str) -> dict[str, int]:
    """Net expense per account NAME (budget categories are account names)
    for the given month, on expense-nature accounts of any locale chart."""
    start, end = month_bounds(month)
    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= start, Transaction.date <= end)
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().all()
    actual_by_cat: dict[str, int] = {}
    for t in txns:
        for ln in t.lines:
            if classify_account_code(ln.account.code) == EXPENSE:
                cat = ln.account.name
                actual_by_cat[cat] = actual_by_cat.get(cat, 0) + max(0, ln.debit - ln.credit)
    return actual_by_cat


def budget_utilization(db: Session, month: str) -> list[dict]:
    """Rows of {category, limit_amount, actual_amount, variance,
    utilization_pct} for every budget limit set in ``month``."""
    limits = db.execute(select(BudgetLimit).where(BudgetLimit.month == month)).scalars().all()
    if not limits:
        return []
    actual_by_cat = expense_actuals_by_category(db, month)
    rows = []
    for b in limits:
        actual = actual_by_cat.get(b.category, 0)
        util = (actual / b.limit_amount * 100.0) if b.limit_amount > 0 else 0.0
        rows.append({
            "month": b.month,
            "category": b.category,
            "limit_amount": b.limit_amount,
            "actual_amount": actual,
            "variance": b.limit_amount - actual,
            "utilization_pct": round(util, 2),
        })
    rows.sort(key=lambda x: x["utilization_pct"], reverse=True)
    return rows
