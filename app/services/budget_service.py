"""Budget actuals — shared by /budgets/actual-vs-budget and the
notification feed's budget alerts.

Expense detection is locale-agnostic via ``classify_account_code`` (Iran
5x/6x, UK 5/7/8/9xxx, and the personal chart's 61xx/62xx), replacing the
old hard-coded ``61``/``62`` prefix check that returned zero actuals for
UK-locale charts. The month is filtered in SQL instead of loading every
transaction into Python.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.account import Account
from app.models.budget import BudgetLimit
from app.models.transaction import Transaction, TransactionLine
from app.services.reporting.common import EXPENSE, classify_account_code


def month_bounds(month: str) -> tuple[date, date]:
    """'YYYY-MM' → (first day, last day), in the calendar the year says:
    '1405-07' is Mehr 1405 (23 Sep – 22 Oct 2026), '2026-09' is September
    (roadmap §3.5 — an Iranian company budgets by Jalali month)."""
    from app.services.calendar_periods import key_bounds
    return key_bounds(month)


def expense_by_account(db: Session, month: str) -> dict[str, tuple[str, int]]:
    """Net expense per expense-nature account in the month, by code:
    {code: (name, amount)}, at base value (a budget is in the company's
    currency — roadmap §4.6)."""
    start, end = month_bounds(month)
    txns = db.execute(
        select(Transaction)
        .where(Transaction.date >= start, Transaction.date <= end, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines).selectinload(TransactionLine.account))
    ).scalars().all()
    out: dict[str, tuple[str, int]] = {}
    for t in txns:
        for ln in t.lines:
            if classify_account_code(ln.account.code) == EXPENSE:
                name, amount = out.get(ln.account.code, (ln.account.name, 0))
                out[ln.account.code] = (name, amount + max(0, (ln.base_debit or 0) - (ln.base_credit or 0)))
    return out


def expense_actuals_by_category(db: Session, month: str) -> dict[str, int]:
    """Net expense per account NAME for the given month, on expense-nature
    accounts of any locale chart."""
    out: dict[str, int] = {}
    for name, amount in expense_by_account(db, month).values():
        out[name] = out.get(name, 0) + amount
    return out


# Persian and Arabic-Indic digits, for a code typed as «۶۱۱۰»
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _as_code(category: str | None) -> str | None:
    """The category as an account code (ASCII digits), or None for a name."""
    c = (category or "").strip().translate(_DIGITS)
    return c if c.isascii() and c.isdigit() else None


def actual_for(category: str, by_account: dict[str, tuple[str, int]]) -> int:
    """What a budget's category spent. A category is an account's name (any
    case) or its code; a code counts its sub-accounts too ("61" → 6110, 6112…).
    The category field offers the chart's codes (#274), and a budget set by
    code read 0 spent — its overspend alert never fired (2026-10-02, #50)."""
    from app.utils.text import fold_fa
    c = _as_code(category)
    if c:
        return sum(amount for code, (_n, amount) in by_account.items() if code.startswith(c))
    key = fold_fa(" ".join((category or "").split())).casefold()
    return sum(amount for name, amount in by_account.values() if fold_fa(" ".join((name or "").split())).casefold() == key)


def budget_utilization(db: Session, month: str) -> list[dict]:
    """Rows of {category, label, limit_amount, actual_amount, variance,
    utilization_pct} for every budget limit set in ``month``."""
    limits = db.execute(select(BudgetLimit).where(BudgetLimit.month == month)).scalars().all()
    if not limits:
        return []
    by_account = expense_by_account(db, month)
    # a code reads with its account's name, as the dashboard shows it ("6110 — Salaries")
    codes = {c for c in (_as_code(b.category) for b in limits) if c}
    names = dict(db.execute(select(Account.code, Account.name).where(Account.code.in_(codes))).all()) if codes else {}
    rows = []
    for b in limits:
        actual = actual_for(b.category, by_account)
        code = _as_code(b.category)
        util = (actual / b.limit_amount * 100.0) if b.limit_amount > 0 else 0.0
        rows.append({
            "id": str(b.id),
            "month": b.month,
            "category": b.category,
            "label": f"{code} — {names[code]}" if code in names else b.category,
            "limit_amount": b.limit_amount,
            "actual_amount": actual,
            "variance": b.limit_amount - actual,
            "utilization_pct": round(util, 2),
        })
    rows.sort(key=lambda x: x["utilization_pct"], reverse=True)
    return rows


def _key_add(key: str, n: int) -> str:
    """'1405-12' + 1 → '1406-01' — in whichever calendar the key is (§3.5)."""
    from app.services.calendar_periods import shift
    y, m = (int(x) for x in key.split("-"))
    ny, nm = shift(y, m, n)
    return f"{ny:04d}-{nm:02d}"


def roll_forward(db: Session, *, from_month: str, months: int = 1, change_pct: float = 0.0,
                 overwrite: bool = False) -> dict:
    """Copy ``from_month``'s budgets into the next ``months`` months, each
    changed by ``change_pct`` % of the source (not compounded), rounded half-up
    to whole units. A category a month already has is kept unless
    ``overwrite``. Returns what was created, updated and left alone."""
    from decimal import ROUND_HALF_UP, Decimal
    if not 1 <= int(months) <= 12:
        raise ValueError("Roll forward 1 to 12 months at a time.")
    if not -99 <= float(change_pct) <= 1000:
        raise ValueError("The change must be between -99 % and +1000 %.")
    source = db.execute(select(BudgetLimit).where(BudgetLimit.month == from_month)
                        .order_by(BudgetLimit.category)).scalars().all()
    if not source:
        raise LookupError(f"There are no budgets in {from_month} to copy.")
    factor = (Decimal(100) + Decimal(repr(float(change_pct)))) / Decimal(100)
    created = updated = kept = 0
    targets = [_key_add(from_month, i) for i in range(1, int(months) + 1)]
    for key in targets:
        have = {b.category.strip().lower(): b for b in
                db.execute(select(BudgetLimit).where(BudgetLimit.month == key)).scalars()}
        for src in source:
            amount = max(1, int((Decimal(src.limit_amount) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
            row = have.get(src.category.strip().lower())
            if row is None:
                db.add(BudgetLimit(month=key, category=src.category, limit_amount=amount))
                created += 1
            elif overwrite and row.limit_amount != amount:
                row.limit_amount = amount
                updated += 1
            else:
                kept += 1
    db.flush()
    return {"from_month": from_month, "months": targets, "created": created, "updated": updated, "kept": kept}

