from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


ASSET = "ASSET"
LIABILITY = "LIABILITY"
EQUITY = "EQUITY"
REVENUE = "REVENUE"
EXPENSE = "EXPENSE"
OTHER = "OTHER"

ACCOUNT_TYPE_FA = {
    ASSET: "دارایی",
    LIABILITY: "بدهی",
    EQUITY: "حقوق مالکانه",
    REVENUE: "درآمد",
    EXPENSE: "هزینه",
    OTHER: "سایر",
}


def classify_account_code(code: str) -> str:
    """Classify an account code to its statement nature. Handles both the
    Iranian chart (11-15 assets, 21-24 liab, 31-33 equity, 41-43 revenue,
    5x/6x expense, 91 memo) and the Sage-style UK chart (0xxx fixed assets,
    1xxx current assets, 2xxx creditors, 3xxx capital, 4xxx turnover,
    5xxx COGS, 7-8xxx overheads/finance, 9xxx tax)."""
    c = (code or "").strip()
    if not c:
        return OTHER
    # Iranian memo / off-balance-sheet accounts — kept for back-compat.
    if c.startswith("91"):
        return OTHER
    head = c[0]
    if head in ("0", "1"):
        return ASSET
    if head == "2":
        return LIABILITY
    if head == "3":
        return EQUITY
    if head == "4":
        return REVENUE
    if head in ("5", "6", "7", "8", "9"):
        return EXPENSE
    return OTHER


def balance_from_turnovers(account_type: str, debit_turnover: int, credit_turnover: int) -> int:
    """
    Unit-testable account net balance:
    - debit-nature accounts (assets/expenses): debit - credit
    - credit-nature accounts (liability/equity/revenue): credit - debit
    """
    if account_type in (ASSET, EXPENSE):
        return int(debit_turnover) - int(credit_turnover)
    if account_type in (LIABILITY, EQUITY, REVENUE):
        return int(credit_turnover) - int(debit_turnover)
    return int(debit_turnover) - int(credit_turnover)


def statement_sign_value(account_type: str, raw_balance: int) -> int:
    """Return positive presentation value for statements."""
    return max(0, int(raw_balance))


@dataclass
class ParsedRange:
    from_date: date
    to_date: date


def default_period(from_date: date | None, to_date: date | None) -> ParsedRange:
    today = date.today()
    if to_date is None:
        to_date = today
    if from_date is None:
        from_date = to_date.replace(day=1)
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    return ParsedRange(from_date=from_date, to_date=to_date)


def period_for_keyword(keyword: str, *, today: date | None = None) -> ParsedRange | None:
    now = today or date.today()
    k = (keyword or "").strip().lower()
    if k in ("today", "امروز"):
        return ParsedRange(now, now)
    if k in ("yesterday", "دیروز"):
        d = now - timedelta(days=1)
        return ParsedRange(d, d)
    if k in ("this month", "این ماه"):
        return ParsedRange(now.replace(day=1), now)
    if k in ("last month", "ماه قبل", "ماه گذشته"):
        first_this = now.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return ParsedRange(last_prev.replace(day=1), last_prev)
    if k in ("this year", "امسال", "از اول سال"):
        return ParsedRange(date(now.year, 1, 1), now)
    if k in ("last week", "هفته قبل", "هفته گذشته"):
        return ParsedRange(now - timedelta(days=7), now)
    if k in ("last 3 months", "three months", "سه ماه اخیر"):
        return ParsedRange(now - timedelta(days=90), now)
    return None
