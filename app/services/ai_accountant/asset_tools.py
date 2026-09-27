"""Read tool: the fixed-asset register (roadmap 2026-09 §4.3) — what the
business owns, book values, and depreciation that is due but not posted."""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.services.ai_accountant.base import BaseTool, ToolContext


class GetFixedAssetsInput(BaseModel):
    as_of: date | None = Field(None, description="Book values as of this date. Defaults to today.")
    include_disposed: bool = Field(False, description="Also list assets already sold or scrapped.")


class GetFixedAssets(BaseTool):
    name = "get_fixed_assets"
    category = "read"
    description = (
        "The fixed-asset register: each asset's cost, accumulated depreciation, book value, method and "
        "life, this month's depreciation, totals per currency and category, and how many months of "
        "depreciation have ended without being posted. Use it for 'what assets do we have', 'book value "
        "of the car', 'دارایی‌های ثابت', 'استهلاک این ماه چقدره', 'is depreciation up to date'. To post "
        "depreciation or record a sale, send the user to the Fixed assets page — this tool changes nothing."
    )
    InputSchema = GetFixedAssetsInput

    async def run(self, ctx: ToolContext, args: GetFixedAssetsInput) -> dict[str, Any]:
        from app.services.fixed_assets import register

        reg = register(ctx.db, as_of=args.as_of, include_disposed=args.include_disposed)
        keep = ("number", "name", "category", "status", "currency", "in_service_on", "method", "life_months",
                "rate_bps", "cost", "accumulated", "net_book_value", "monthly_charge", "fully_depreciated",
                "disposed_on", "disposal_proceeds")
        out = {"as_of": reg["as_of"], "calendar": reg["calendar"], "totals": reg["totals"],
               "by_category": reg["by_category"], "due": reg["due"],
               "assets": [{k: a[k] for k in keep} for a in reg["assets"][:50]], "count": len(reg["assets"])}
        if reg["due"]["months"]:
            out["note"] = ("Depreciation for ended months is not posted yet — the user can post it under "
                           "Fixed assets → Month-end depreciation.")
        if not reg["assets"]:
            out["note"] = "No fixed assets are registered yet."
        return out


def register_asset_tools(registry) -> None:
    registry.register(GetFixedAssets())
