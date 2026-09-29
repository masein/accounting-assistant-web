"""Budget and month-end tools for the AI accountant (roadmap 2026-09 §5.1).

Read: get_budget_status (budget vs actual for a month), get_close_checklist
(what a month-end close still needs — the checklist of the close pack).
Proposal: propose_set_budget (a monthly limit for an expense category, for one
month or several), executed in payroll_execute.py.

Months are keys in the company's calendar: ``1405-07`` is Mehr for an
Iranian company, ``2026-09`` September for a UK one.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from .base import BaseTool, ToolContext, ToolError
from .time_tools import _register

MONTH = r"^\d{4}-\d{2}$"


def _this_month(ctx: ToolContext) -> str:
    from app.services.calendar_periods import company_calendar, month_key
    return month_key(_date.today(), company_calendar(ctx.db))


def _check_month(key: str) -> tuple[_date, _date]:
    from app.services.calendar_periods import key_bounds
    try:
        return key_bounds(key)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"{key!r} isn't a month (YYYY-MM).", code="bad_month") from e


def expense_category(ctx: ToolContext, text: str):
    """An expense account by code or name (exact, then the one that contains it)."""
    from app.models.account import Account
    from app.services.reporting.common import EXPENSE, classify_account_code
    text = (text or "").strip()
    if not text:
        raise ToolError("Which expense category?", code="category_not_found")
    accounts = [a for a in ctx.db.execute(select(Account)).scalars() if classify_account_code(a.code) == EXPENSE]
    for pick in (lambda a: a.code == text, lambda a: a.name.lower() == text.lower(),
                 lambda a: text.lower() in a.name.lower()):
        hits = [a for a in accounts if pick(a)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ToolError(f"{len(hits)} expense accounts match {text!r}: " + ", ".join(
                f"{a.code} {a.name}" for a in hits[:8]) + " — ask which one.", code="category_ambiguous")
    raise ToolError(f"No expense account matches {text!r}. Budgets are set on expense accounts — "
                    "search_accounts lists them.", code="category_not_found")


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------

class GetBudgetStatusInput(BaseModel):
    month: str | None = Field(None, pattern=MONTH, description="YYYY-MM in the company's calendar; default this month.")


class GetBudgetStatus(BaseTool):
    name = "get_budget_status"
    category = "read"
    description = (
        "Budget vs actual for a month: each expense category's limit, what was spent, what's left and the % used, "
        "flagging the ones over or near their limit; with no budgets, the month's biggest expense categories. Use "
        "for 'are we over budget?', 'how much of the marketing budget is left?'. Amounts in the base currency. Pure read."
    )
    InputSchema = GetBudgetStatusInput

    async def run(self, ctx: ToolContext, args: GetBudgetStatusInput) -> dict[str, Any]:
        from app.services.budget_service import budget_utilization, expense_actuals_by_category
        from app.services.calendar_periods import month_label
        from app.services.fx_base import base_currency
        key = args.month or _this_month(ctx)
        start, end = _check_month(key)
        rows = budget_utilization(ctx.db, key)
        out = {"month": key, "label": month_label(key), "from_date": start.isoformat(), "to_date": end.isoformat(),
               "currency": base_currency(ctx.db)}
        if not rows:
            actual = expense_actuals_by_category(ctx.db, key)
            top = sorted(actual.items(), key=lambda kv: -kv[1])[:8]
            return {**out, "budgets": [], "note": "No budgets are set for this month.",
                    "top_expenses": [{"category": k, "actual": v} for k, v in top if v]}
        budgets = [{"category": r["category"], "budget": r["limit_amount"], "actual": r["actual_amount"],
                    "left": r["variance"], "used_pct": r["utilization_pct"],
                    "state": "over" if r["utilization_pct"] > 100 else "near" if r["utilization_pct"] >= 80 else "ok"}
                   for r in rows]
        total_b = sum(b["budget"] for b in budgets)
        total_a = sum(b["actual"] for b in budgets)
        return {**out, "budgets": budgets, "total_budget": total_b, "total_actual": total_a,
                "total_left": total_b - total_a, "over": [b["category"] for b in budgets if b["state"] == "over"]}


class GetCloseChecklistInput(BaseModel):
    month: str | None = Field(None, pattern=MONTH, description="YYYY-MM in the company's calendar; default last month.")


class GetCloseChecklist(BaseTool):
    name = "get_close_checklist"
    category = "read"
    description = (
        "What a month-end close still needs: debits equal credits, bank lines matched, draft invoices, the pay run "
        "posted, depreciation run, entries waiting for an exchange rate, the books locked — each done, needs "
        "attention or for information. Use for 'can we close August?', 'what's left for month end?'. The full close "
        "pack (PDF + Excel + journal) downloads from Manager reports → Monthly close pack. Pure read."
    )
    InputSchema = GetCloseChecklistInput

    async def run(self, ctx: ToolContext, args: GetCloseChecklistInput) -> dict[str, Any]:
        from app.services.reporting.close_pack import ExportError, checklist, resolve_month, summary
        try:
            m = resolve_month(ctx.db, args.month, documents=False)
        except ExportError as e:
            raise ToolError(str(e), code="bad_month") from e
        items = checklist(ctx.db, m)
        return {"month": m.key, "label": m.label, "from_date": m.start.isoformat(), "to_date": m.end.isoformat(),
                "items": [{"key": i["key"], "state": i["state"], "item": i["item"], "detail": i["detail"]} for i in items],
                "open": sum(1 for i in items if i["state"] == "warn"), "summary": summary(items, m.lang),
                "pack": "Manager reports → Monthly close pack"}


# ---------------------------------------------------------------------------
# PROPOSALS
# ---------------------------------------------------------------------------

class ProposeSetBudgetInput(BaseModel):
    category: str = Field(..., description="The expense account (name or code) the limit is for.")
    amount: int = Field(..., gt=0, description="The monthly limit, whole units of the base currency.")
    month: str | None = Field(None, pattern=MONTH, description="First month (YYYY-MM, company calendar); default this month.")
    months: int = Field(1, ge=1, le=24, description="How many consecutive months to set (e.g. 12 for the year).")


class ProposeSetBudget(BaseTool):
    name = "propose_set_budget"
    category = "proposal"
    description = (
        "Set (or change) a monthly budget limit for an expense category — one month, or the same limit for several "
        "months in a row. Use for 'budget 50 million a month for marketing', 'set rent's budget to 2,000 for the "
        "year'. Nothing is posted. Returns a confirm card showing the old and new limits."
    )
    InputSchema = ProposeSetBudgetInput

    async def run(self, ctx: ToolContext, args: ProposeSetBudgetInput) -> dict[str, Any]:
        from app.models.budget import BudgetLimit
        from app.services.budget_service import _key_add
        from app.services.fx_base import base_currency
        acc = expense_category(ctx, args.category)
        first = args.month or _this_month(ctx)
        _check_month(first)
        keys = [first] + [_key_add(first, i) for i in range(1, args.months)]
        existing = {b.month: int(b.limit_amount) for b in ctx.db.execute(
            select(BudgetLimit).where(BudgetLimit.month.in_(keys), BudgetLimit.category.ilike(acc.name))).scalars()}
        changes = [{"month": k, "before": existing.get(k), "after": args.amount} for k in keys]
        payload = {"category": acc.name, "account_code": acc.code, "amount": args.amount, "months": keys,
                   "currency": base_currency(ctx.db)}
        token = _register(ctx, self.name, payload)
        span = keys[0] if len(keys) == 1 else f"{keys[0]} to {keys[-1]} ({len(keys)} months)"
        changed = [c for c in changes if c["before"] is not None and c["before"] != args.amount]
        summary = (f"Budget for {acc.code} {acc.name}: {args.amount:,} {payload['currency']} a month, {span}."
                   + (f" Replaces {len(changed)} existing limit(s)." if changed else ""))
        return {"confirmation_token": str(token), "status": "pending", "summary": summary,
                "preview": {**payload, "changes": changes}}


def register_period_tools(registry) -> None:
    registry.register(GetBudgetStatus())
    registry.register(GetCloseChecklist())
    registry.register(ProposeSetBudget())
