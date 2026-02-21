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
    c = (code or "").strip()
    if c.startswith("11") or c.startswith("12") or c.startswith("13") or c.startswith("14") or c.startswith("15"):
        return ASSET
    if c.startswith("21") or c.startswith("22") or c.startswith("23") or c.startswith("24"):
        return LIABILITY
    if c.startswith("31") or c.startswith("32") or c.startswith("33"):
        return EQUITY
    if c.startswith("41") or c.startswith("42") or c.startswith("43"):
        return REVENUE
    if c.startswith("51") or c.startswith("52") or c.startswith("53") or c.startswith("61") or c.startswith("62"):
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
