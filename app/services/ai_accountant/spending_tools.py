"""Read tool: how much was spent / earned in a period named the way people
say it ("this month", "این ماه", "last year").

The model is unreliable at turning time words into dates — for an Iranian
tenant it answered "این ماه چقدر خرج کردم؟" with "nothing recorded" right
after an expense was posted (it queried the wrong range) and dated "today"
as ۲۴ مهر instead of ۲ مهر (it mixed the Gregorian day into the Jalali
month). So the period is resolved server-side in the company's display
calendar and the answer comes back with the resolved dates and a period
label in both calendars for the model to quote verbatim.
"""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import jdatetime
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.account import Account
from app.models.transaction import Transaction, TransactionLine
from app.services.ai_accountant.base import BaseTool, ToolContext, ToolError
from app.services.locale_service import get_display_calendar
from app.services.reporting.common import EXPENSE, REVENUE, classify_account_code
from app.utils.jalali import (
    JALALI_MONTH_NAMES_EN,
    JALALI_MONTH_NAMES_FA,
    format_jalali,
    format_jalali_long,
    to_persian_digits,
)

Period = Literal[
    "today", "yesterday", "this_week", "last_week", "this_month", "last_month",
    "this_year", "last_year", "custom",
]


@dataclass
class ResolvedPeriod:
    from_date: date
    to_date: date
    calendar: str
    label_en: str
    label_fa: str

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "from": self.from_date.isoformat(),
            "to": self.to_date.isoformat(),
            "calendar": self.calendar,
            "label_en": self.label_en,
            "label_fa": self.label_fa,
        }
        if self.calendar == "jalali":
            d["from_jalali"] = format_jalali(self.from_date)
            d["to_jalali"] = format_jalali(self.to_date)
        return d


def _j(d: date) -> jdatetime.date:
    return jdatetime.date.fromgregorian(date=d)


def _j_first(y: int, m: int) -> date:
    g = jdatetime.date(y, m, 1).togregorian()
    return date(g.year, g.month, g.day)


def _j_last(y: int, m: int) -> date:
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return _j_first(ny, nm) - timedelta(days=1)


def _fa_range(a: date, b: date) -> str:
    return f"{format_jalali_long(a)} تا {format_jalali_long(b)}"


def resolve_period(
    period: str,
    today: date,
    calendar: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> ResolvedPeriod:
    """Turn a period keyword into inclusive dates in the given calendar
    ("jalali" or "gregorian"). Weeks start on Saturday in the Jalali
    calendar and on Monday otherwise."""
    cal = "jalali" if (calendar or "").lower() == "jalali" else "gregorian"

    if period == "custom":
        if from_date is None or to_date is None:
            raise ToolError("period='custom' needs both from_date and to_date", code="bad_period")
        a, b = from_date, to_date
    elif period == "today":
        a = b = today
    elif period == "yesterday":
        a = b = today - timedelta(days=1)
    elif period in ("this_week", "last_week"):
        start_weekday = 5 if cal == "jalali" else 0  # Saturday / Monday
        offset = (today.weekday() - start_weekday) % 7
        a = today - timedelta(days=offset)
        b = today
        if period == "last_week":
            b = a - timedelta(days=1)
            a = b - timedelta(days=6)
    elif cal == "jalali":
        jt = _j(today)
        if period == "this_month":
            a, b = _j_first(jt.year, jt.month), today
        elif period == "last_month":
            y, m = (jt.year - 1, 12) if jt.month == 1 else (jt.year, jt.month - 1)
            a, b = _j_first(y, m), _j_last(y, m)
        elif period == "this_year":
            a, b = _j_first(jt.year, 1), today
        elif period == "last_year":
            a, b = _j_first(jt.year - 1, 1), _j_first(jt.year, 1) - timedelta(days=1)
        else:
            raise ToolError(f"unknown period {period!r}", code="bad_period")
    else:
        if period == "this_month":
            a, b = today.replace(day=1), today
        elif period == "last_month":
            first_this = today.replace(day=1)
            b = first_this - timedelta(days=1)
            a = b.replace(day=1)
        elif period == "this_year":
            a, b = date(today.year, 1, 1), today
        elif period == "last_year":
            a, b = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        else:
            raise ToolError(f"unknown period {period!r}", code="bad_period")

    if a > b:
        raise ToolError("from_date is after to_date", code="bad_period")

    if cal == "jalali":
        ja, jb = _j(a), _j(b)
        if period in ("this_month", "last_month") and (ja.year, ja.month) == (jb.year, jb.month):
            head_fa = f"{JALALI_MONTH_NAMES_FA[ja.month - 1]} {to_persian_digits(ja.year)}"
            head_en = f"{JALALI_MONTH_NAMES_EN[ja.month - 1]} {ja.year}"
        elif period in ("this_year", "last_year"):
            head_fa, head_en = f"سال {to_persian_digits(ja.year)}", f"Jalali year {ja.year}"
        else:
            head_fa = head_en = ""
        label_fa = (f"{head_fa} ({_fa_range(a, b)})" if head_fa else _fa_range(a, b))
        label_en = (f"{head_en} ({a.isoformat()} to {b.isoformat()})" if head_en
                    else f"{a.isoformat()} to {b.isoformat()}")
    else:
        if period in ("this_month", "last_month") and (a.year, a.month) == (b.year, b.month):
            head_en = f"{_cal.month_name[a.month]} {a.year}"
        elif period in ("this_year", "last_year"):
            head_en = f"Year {a.year}"
        else:
            head_en = ""
        label_en = (f"{head_en} ({a.isoformat()} to {b.isoformat()})" if head_en
                    else f"{a.isoformat()} to {b.isoformat()}")
        label_fa = label_en
    return ResolvedPeriod(from_date=a, to_date=b, calendar=cal, label_en=label_en, label_fa=label_fa)


class GetSpendingSummaryInput(BaseModel):
    period: Period = Field(
        "this_month",
        description=(
            "Which period, as the user said it: this_month for 'این ماه / this month', "
            "last_month, this_year for 'امسال / this year', last_year, this_week, last_week, "
            "today, yesterday. Use custom with from_date/to_date only for explicit dates."
        ),
    )
    from_date: date | None = Field(None, description="Only with period='custom'.")
    to_date: date | None = Field(None, description="Only with period='custom'.")
    kind: Literal["spending", "income"] = Field(
        "spending", description="spending = expense categories; income = revenue categories."
    )
    category_code: str | None = Field(
        None,
        description="Optional account code or prefix to restrict to one category (e.g. food). Omit for the total.",
    )


class GetSpendingSummary(BaseTool):
    name = "get_spending_summary"
    category = "read"
    description = (
        "How much was spent (or earned) in a period named the way the user said it — "
        "'this month', 'این ماه', 'last month', 'امسال', 'this year'. Resolves the period "
        "in the company's own calendar (Jalali for Iranian tenants) so you never compute "
        "dates yourself, and returns the total, a breakdown by category, the transaction "
        "count and the resolved period with labels in both calendars. ALWAYS use this for "
        "'how much did I spend/earn <time word>' questions, and quote the period label from "
        "the result. Pure read."
    )
    InputSchema = GetSpendingSummaryInput

    async def run(self, ctx: ToolContext, args: GetSpendingSummaryInput) -> dict[str, Any]:
        from app.services.fx_service import get_reporting_currency

        today = date.today()
        cal = get_display_calendar(ctx.db)
        period = resolve_period(args.period, today, cal, args.from_date, args.to_date)
        wanted = EXPENSE if args.kind == "spending" else REVENUE

        stmt = (
            select(Transaction, TransactionLine, Account)
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(
                Transaction.deleted_at.is_(None),
                Transaction.date >= period.from_date,
                Transaction.date <= period.to_date,
            )
        )
        if args.category_code:
            stmt = stmt.where(Account.code.like(f"{args.category_code.strip()}%"))

        by_code: dict[str, dict[str, Any]] = {}
        txn_ids: set = set()
        for txn, line, acc in ctx.db.execute(stmt).all():
            if classify_account_code(acc.code) != wanted:
                continue
            d, c = int(line.debit or 0), int(line.credit or 0)
            amount = (d - c) if wanted == EXPENSE else (c - d)
            slot = by_code.setdefault(acc.code, {"category_code": acc.code, "category_name": acc.name, "amount": 0, "count": 0})
            slot["amount"] += amount
            slot["count"] += 1
            txn_ids.add(txn.id)

        categories = sorted((v for v in by_code.values() if v["amount"]), key=lambda v: -v["amount"])
        total = sum(v["amount"] for v in categories)
        out: dict[str, Any] = {
            "kind": args.kind,
            "period": period.as_dict(),
            "today": today.isoformat(),
            "currency": get_reporting_currency(ctx.db),
            "total": total,
            "transaction_count": len(txn_ids),
            "by_category": categories[:12],
        }
        if cal == "jalali":
            out["today_jalali"] = format_jalali(today)
            out["today_jalali_long"] = format_jalali_long(today)
        if not categories:
            out["note"] = f"No {args.kind} recorded between {period.from_date.isoformat()} and {period.to_date.isoformat()}."
        return out


def register_spending_tools(registry) -> None:
    registry.register(GetSpendingSummary())
