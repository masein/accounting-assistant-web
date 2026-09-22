"""Read tool: the proactive insights, for the assistant to be the one who
speaks first about what changed in the books."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.ai_accountant.base import BaseTool, ToolContext


class GetInsightsInput(BaseModel):
    language: str | None = Field(
        None, description="Language for the wording (en, fa, es, ar). Defaults to the user's language.",
    )


class GetInsights(BaseTool):
    name = "get_insights"
    category = "read"
    description = (
        "What the system has noticed in the books that the user should hear about, ranked by "
        "importance: payroll changes and new employees, an expense account running far above "
        "its usual level, a revenue drop, short cash runway, an unusually large payment to a "
        "supplier, a bank statement that is due for upload, growing receivables, a usual "
        "recurring payment that hasn't shown up. Each item has a title, a plain-language message "
        "with the figures, the page to open and structured data. Call it when the user asks how "
        "things are going, what to look at, or opens with a greeting and no task; then summarise "
        "the top items in your own words and offer one concrete next step for each."
    )
    InputSchema = GetInsightsInput

    async def run(self, ctx: ToolContext, args: GetInsightsInput) -> dict[str, Any]:
        from app.services.insight_service import SUPPORTED_LANGUAGES, insights_payload

        lang = (args.language or getattr(ctx, "lang", None) or "en").lower()
        if lang not in SUPPORTED_LANGUAGES:
            lang = "en"
        payload = insights_payload(ctx.db, lang)
        payload["count"] = len(payload["insights"])
        if not payload["insights"]:
            payload["note"] = "Nothing unusual right now — the books look steady."
        return payload


def register_insight_tools(registry) -> None:
    registry.register(GetInsights())
