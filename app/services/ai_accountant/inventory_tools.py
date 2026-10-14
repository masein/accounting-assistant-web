"""Read tool: stock on hand, its value under the company's costing method,
and what is at or below its reorder point (roadmap 2026-09 §4.4)."""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.services.ai_accountant.base import BaseTool, ToolContext


class GetInventoryInput(BaseModel):
    as_of: date | None = Field(None, description="Stock and values as of this date. Defaults to today.")
    low_stock_only: bool = Field(False, description="Only the items at or below their reorder point.")


class GetInventory(BaseTool):
    name = "get_inventory"
    category = "read"
    description = (
        "Stock on hand per item with its unit cost and value (weighted average or FIFO, the company's "
        "setting), cost of goods sold to date, the total under the other method for comparison, and the "
        "items at or below their reorder point with a suggested order quantity. When stock posts to the "
        "books, ``books`` gives the stock account's balance beside the valuation: say the value is what the "
        "books carry, or by how much they differ (then: Inventory → Post now). Use it for 'how much "
        "stock do we have', 'value of inventory', 'what do I need to reorder', 'موجودی انبار', "
        "'کالاهایی که باید سفارش بدم'. Pure read."
    )
    InputSchema = GetInventoryInput

    async def run(self, ctx: ToolContext, args: GetInventoryInput) -> dict[str, Any]:
        from app.services.inventory_costing import low_stock, valuation

        if args.low_stock_only:
            rows = low_stock(ctx.db, as_of=args.as_of)
            return {"count": len(rows), "items": rows[:50],
                    "note": None if rows else "Nothing is at or below its reorder point."}
        v = valuation(ctx.db, as_of=args.as_of)
        keep = ("name", "sku", "unit", "on_hand", "unit_cost", "value", "cogs", "reorder_level", "below_reorder",
                "oversold")
        return {"as_of": v["as_of"], "method": v["method"], "totals": v["totals"], "other_method": v["other_method"],
                "items": [{k: r[k] for k in keep} for r in v["rows"][:60]], "count": len(v["rows"]),
                "books": _books(ctx.db, v)}


def _books(db, v: dict) -> dict[str, Any]:
    """Stock in the books (docs/PLAN_INVENTORY_GL.md): whether it posts, and
    the stock account's balance on the same day beside the valuation."""
    from app.core.money import plain, to_decimal
    from app.services import inventory_ledger
    start = inventory_ledger.start_date(db)
    if start is None:
        return {"posting": False, "note": "Stock isn't posted to the books: the balance sheet carries none of it."}
    books = inventory_ledger.books_value(db, date.fromisoformat(v["as_of"]))
    value = plain(v["totals"]["value"])
    return {"posting": True, "from": start.isoformat(), "books_value": books, "valuation": value,
            "difference": plain(to_decimal(books) - to_decimal(value)), "agrees": plain(books) == value}


def register_inventory_tools(registry) -> None:
    registry.register(GetInventory())
