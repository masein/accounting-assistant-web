"""Each currency a person's books hold, on its own (docs/PLAN_CURRENCIES.md phase 2).

My finances and the phone's widget showed one figure in the books' currency.
A person keeps rials at a bank and Omani rials in a wallet, each as it is, and
Omani rials with no rate counted as nothing there. Here each currency has its
own cash, and this month's income, spending and what was saved, in its own
amounts — never exchanged, never added together.

The order is the one a person reads them in: the currencies that moved this
month, the books' own first and then the busiest, then the rest the books
hold."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.money import plain


def overview(db: Session, *, lang: str = "en", today: date | None = None) -> dict[str, Any]:
    from app.core.currencies import name
    from app.services.calendar_periods import bounds, company_calendar, month_key, month_of
    from app.services.fx_base import base_currency
    from app.services.report_card import _flows, currencies_moved
    from app.services.reporting.repository import distinct_currencies

    today = today or date.today()
    base = base_currency(db)
    cal = company_calendar(db)
    start, _end = bounds(*month_of(today, cal), cal)
    moved = currencies_moved(db, start, today)
    held = sorted({(c or base).strip().upper() for c in distinct_currencies(db)}, key=lambda c: (c != base, c))
    order = moved + [c for c in held if c not in moved] or [base]
    cash = _cash_by_currency(db, today, base)
    rows = []
    for ccy in order:
        income, spent, _cats = _flows(db, start, today, ccy)
        rows.append({"currency": ccy, "name": name(ccy, lang), "cash": cash.get(ccy, 0),
                     "income": plain(income), "spent": plain(spent), "saved": plain(income - spent)})
    return {"books_currency": base, "month": month_key(today, cal), "currencies": rows}


def _cash_by_currency(db: Session, today: date, base: str) -> dict[str, Any]:
    """The money a person holds, per currency, as the chat counts it: the bank (1110) and cash on hand
    (1120) with the places under them — a wallet, a vault (scenario AB4). The dashboard's cash counts a
    company's, and left a person's wallet out."""
    from sqlalchemy import func, select

    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    from app.services.ai_accountant.cash_tools import cash_account_codes
    from app.services.locale_service import get_reporting_locale
    codes = cash_account_codes(db, locale=get_reporting_locale(db), mode="personal")
    rows = db.execute(
        select(Transaction.currency, func.coalesce(func.sum(TransactionLine.debit), 0),
               func.coalesce(func.sum(TransactionLine.credit), 0))
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Account.code.in_(codes), Transaction.deleted_at.is_(None), Transaction.date <= today)
        .group_by(Transaction.currency)).all()            # the raw column: Postgres (#371)
    out: dict[str, Any] = {}
    for ccy, d, c in rows:
        key = (ccy or base).strip().upper()
        out[key] = plain(out.get(key, 0) + plain(d) - plain(c))
    return out
