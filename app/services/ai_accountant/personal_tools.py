"""Personal-mode read tools (roadmap 2026-09 §4.12): the monthly report card
and the savings goals, as the "My finances" page shows them."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .base import BaseTool, ToolContext, ToolError


class GetReportCardInput(BaseModel):
    month: str | None = Field(None, pattern=r"^\d{4}-\d{2}$",
                              description="YYYY-MM in the user's calendar (1405-06 = Shahrivar); default last month.")


class GetReportCard(BaseTool):
    name = "get_report_card"
    category = "read"
    description = (
        "The user's monthly report card: income, spending, what they saved and their savings rate, against last month "
        "and the three-month average; the biggest spending categories and what rose; budgets kept; how net worth "
        "changed; savings goals; three checks (saved 10 %+, spent no more than usual, inside the budgets). Use for "
        "'how did I do last month?', 'کارنامه این ماهم چطوره؟', 'did I save anything in Shahrivar?'. Pure read."
    )
    InputSchema = GetReportCardInput

    async def run(self, ctx: ToolContext, args: GetReportCardInput) -> dict[str, Any]:
        from app.services.report_card import report_card
        try:
            card = report_card(ctx.db, args.month)
        except (ValueError, KeyError) as e:
            raise ToolError(f"{args.month!r} isn't a month (YYYY-MM).", code="bad_month") from e
        return card


class GetSavingsGoalsInput(BaseModel):
    include_archived: bool = Field(False, description="Include goals the user put away.")


class GetSavingsGoals(BaseTool):
    name = "get_savings_goals"
    category = "read"
    description = (
        "The user's savings goals: target, saved so far (the account's value today), what's left, what each month "
        "still needs to reach it by its date, their recent pace and whether they're on track. Use for 'am I on track "
        "for the car?', 'how far am I from my emergency fund?'. Goals are added on the My finances page. Pure read."
    )
    InputSchema = GetSavingsGoalsInput

    async def run(self, ctx: ToolContext, args: GetSavingsGoalsInput) -> dict[str, Any]:
        from app.services.personal_goals import list_goals
        goals = list_goals(ctx.db, include_archived=args.include_archived)
        return {"goals": goals, "count": len(goals),
                "note": None if goals else "No savings goals yet — they're added on the My finances page."}


class GetNetWorthInput(BaseModel):
    pass


class GetNetWorth(BaseTool):
    name = "get_net_worth"
    category = "read"
    description = (
        "The user's net worth today: what they own minus what they owe, with gold and foreign currency restated at "
        "today's rates (and what that revaluation gained), each holding with its quantity, and the month-end trend "
        "over the last twelve months. Use for 'how much am I worth?', 'دارایی خالص من چقدره؟', 'how much is my gold "
        "worth now?'. Holdings and rates are kept on the My finances page. Pure read."
    )
    InputSchema = GetNetWorthInput

    async def run(self, ctx: ToolContext, args: GetNetWorthInput) -> dict[str, Any]:
        from datetime import date as _date, timedelta
        from app.services.net_worth_service import compute_net_worth
        nw = compute_net_worth(ctx.db)

        def month_end(key: str) -> str:
            y, m = int(key[:4]), int(key[5:7])
            first_next = _date(y + (m == 12), m % 12 + 1, 1)
            return min(first_next - timedelta(days=1), nw.as_of).isoformat()

        def line(item) -> dict[str, Any]:
            return {"account_code": item.account_code, "account_name": item.account_name, "book_value": item.book_value,
                    "market_value": item.market_value, "unrealized_gain": item.unrealized_gain, "revalued": item.revalued,
                    "unit": item.unit, "quantity": item.quantity, "rate": item.rate}
        return {"as_of": nw.as_of.isoformat(), "currency": nw.currency, "net_worth": nw.net_worth,
                "total_assets": nw.total_assets, "total_liabilities": nw.total_liabilities,
                "unrealized_gain": nw.unrealized_gain, "assets": [line(x) for x in nw.assets],
                "liabilities": [line(x) for x in nw.liabilities],
                "trend": [{"period": p, "date": month_end(p), "value": v} for p, v in nw.trend],
                "missing_rates": sorted(set(nw.missing_rates))}


def register_personal_tools(registry) -> None:
    registry.register(GetReportCard())
    registry.register(GetSavingsGoals())
    registry.register(GetNetWorth())
