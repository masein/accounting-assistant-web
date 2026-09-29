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


def register_personal_tools(registry) -> None:
    registry.register(GetReportCard())
    registry.register(GetSavingsGoals())
