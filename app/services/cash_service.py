"""Single source of truth for "cash on hand".

The owner dashboard (``app/api/reports.py``) and the CFO/CEO intelligence
layer (``app/services/cfo_intelligence.py``) both surface a "cash on hand"
figure. They used to compute it two different ways — the dashboard summed
the all-time net balance of every cash/bank account, while the CFO engine
only summed cash movements inside its trailing 12-month window — so the
same business showed two different numbers (e.g. 84,377 vs 41,149 GBP for
the UK demo).

This module fixes that by defining ONE meaning: **the net balance of every
cash/bank account as of a given date** (debit − credit), currency-filtered,
excluding soft-deleted transactions. Both callers import ``cash_on_hand``
so the metric can never drift again.

Which account codes count as "cash/bank" is chart-of-accounts specific, so
the predicate is locale-aware (mirrors the dashboard's ``_cash_predicate``).
"""
from __future__ import annotations

from datetime import date
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine


def cash_account_predicate(locale: str | None) -> Callable[[str], bool]:
    """Return ``code -> bool`` selecting the cash/bank accounts for the
    active reporting locale. Kept identical to the dashboard's historical
    selection so reconciling the figure doesn't move the dashboard number.

    * UK (Sage chart): 1200 current, 1210 deposit, 1220 petty cash.
    * Iranian / default chart: 1110 موجودی نقد و بانک.
    """
    if (locale or "").strip().lower() == "uk":
        return lambda c: c.startswith(("120", "121", "122"))
    return lambda c: c == "1110"


def cash_on_hand(
    db: Session,
    *,
    locale: str | None,
    currency: str | None = None,
    as_of: date | None = None,
) -> int:
    """True cash on hand: the net balance (debit − credit) of every
    cash/bank account up to ``as_of`` (defaults to today), in the given
    reporting currency, excluding soft-deleted transactions.

    Returns 0 when the chart has no matching cash account.
    """
    is_cash = cash_account_predicate(locale)
    cash_account_ids = [
        a.id for a in db.execute(select(Account)).scalars().all() if is_cash(a.code or "")
    ]
    if not cash_account_ids:
        return 0

    q = (
        select(func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0))
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .where(TransactionLine.account_id.in_(cash_account_ids))
        .where(Transaction.deleted_at.is_(None))
    )
    if as_of is not None:
        q = q.where(Transaction.date <= as_of)
    if currency:
        q = q.where(Transaction.currency == currency)
    return int(db.execute(q).scalar() or 0)
