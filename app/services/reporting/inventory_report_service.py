from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.inventory import InventoryItem, InventoryMovement, InventoryMovementType
from app.schemas.manager_report import (
    InventoryBalanceResponse,
    InventoryBalanceRow,
    InventoryItemCreate,
    InventoryItemRead,
    InventoryMovementCreate,
    InventoryMovementRead,
    InventoryMovementResponse,
    ReportPeriod,
)
from app.services.reporting.common import default_period
from app.services.reporting.repository import (
    inventory_movements_for_balance,
    list_inventory_items,
    paged_inventory_movements,
)


@dataclass
class ItemAccumulator:
    qty_in: float = 0.0
    qty_out: float = 0.0
    on_hand: float = 0.0
    inventory_value: int = 0
    cogs: int = 0

    @property
    def avg_cost(self) -> int:
        if self.on_hand <= 0:
            return 0
        return int(round(self.inventory_value / self.on_hand))


def apply_inventory_movement(acc: ItemAccumulator, movement_type: str, quantity: float, unit_cost: int) -> ItemAccumulator:
    """
    Unit-testable weighted-average inventory calculator.
    """
    qty = float(quantity or 0)
    cost = int(unit_cost or 0)
    if qty <= 0:
        return acc
    if movement_type == InventoryMovementType.IN.value:
        acc.qty_in += qty
        acc.on_hand += qty
        acc.inventory_value += int(round(qty * cost))
        return acc
    if movement_type == InventoryMovementType.OUT.value:
        acc.qty_out += qty
        use_cost = cost if cost > 0 else acc.avg_cost
        cogs_value = int(round(qty * use_cost))
        acc.cogs += cogs_value
        acc.on_hand = max(0.0, acc.on_hand - qty)
        acc.inventory_value = max(0, acc.inventory_value - cogs_value)
        return acc
    # ADJUSTMENT: positive quantity with explicit valuation.
    acc.qty_in += qty
    acc.on_hand += qty
    acc.inventory_value += int(round(qty * cost))
    return acc


class InventoryReportService:
    def __init__(self, db: Session):
        self.db = db

    def create_item(self, payload: InventoryItemCreate) -> InventoryItemRead:
        row = InventoryItem(sku=(payload.sku or "").strip() or None, name=payload.name.strip(), unit=(payload.unit or "unit").strip())
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return InventoryItemRead.model_validate(row)

    def list_items(self) -> list[InventoryItemRead]:
        return [InventoryItemRead.model_validate(x) for x in list_inventory_items(self.db)]

    def add_movement(self, payload: InventoryMovementCreate) -> InventoryMovementRead:
        item = self.db.get(InventoryItem, payload.item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        typ = (payload.movement_type or "").strip().upper()
        if typ not in (InventoryMovementType.IN.value, InventoryMovementType.OUT.value, InventoryMovementType.ADJUSTMENT.value):
            raise HTTPException(status_code=400, detail="movement_type must be IN, OUT, or ADJUSTMENT")
        row = InventoryMovement(
            item_id=payload.item_id,
            movement_date=payload.movement_date,
            movement_type=InventoryMovementType(typ),
            quantity=float(payload.quantity),
            unit_cost=int(payload.unit_cost or 0),
            reference=(payload.reference or "").strip() or None,
            description=(payload.description or "").strip() or None,
            invoice_id=payload.invoice_id,
            transaction_id=payload.transaction_id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return InventoryMovementRead(
            id=row.id,
            item_id=row.item_id,
            item_name=item.name,
            movement_date=row.movement_date,
            movement_type=row.movement_type.value,
            quantity=float(row.quantity),
            unit_cost=int(row.unit_cost or 0),
            movement_value=int(round(float(row.quantity) * int(row.unit_cost or 0))),
            reference=row.reference,
            description=row.description,
        )

    def movement_report(
        self,
        from_date: date | None,
        to_date: date | None,
        page: int = 1,
        page_size: int = 100,
        item_id: UUID | None = None,
    ) -> InventoryMovementResponse:
        period = default_period(from_date, to_date)
        total, rows = paged_inventory_movements(self.db, period.from_date, period.to_date, page, page_size, item_id=item_id)
        mapped = [
            InventoryMovementRead(
                id=mv.id,
                item_id=item.id,
                item_name=item.name,
                movement_date=mv.movement_date,
                movement_type=mv.movement_type.value,
                quantity=float(mv.quantity),
                unit_cost=int(mv.unit_cost or 0),
                movement_value=int(round(float(mv.quantity) * int(mv.unit_cost or 0))),
                reference=mv.reference,
                description=mv.description,
            )
            for mv, item in rows
        ]
        return InventoryMovementResponse(
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            page=page,
            page_size=page_size,
            total=total,
            rows=mapped,
        )

    def balance_report(self, to_date: date | None = None) -> InventoryBalanceResponse:
        period = default_period(None, to_date)
        rows = inventory_movements_for_balance(self.db, period.to_date)
        item_map: dict[UUID, dict] = {}
        stats: dict[UUID, ItemAccumulator] = defaultdict(ItemAccumulator)
        for mv, item in rows:
            item_map[item.id] = {"name": item.name, "sku": item.sku, "unit": item.unit}
            acc = stats[item.id]
            apply_inventory_movement(acc, mv.movement_type.value, float(mv.quantity), int(mv.unit_cost or 0))

        out: list[InventoryBalanceRow] = []
        total_value = 0
        total_cogs = 0
        total_qty = 0.0
        for item_id, acc in stats.items():
            meta = item_map[item_id]
            row = InventoryBalanceRow(
                item_id=item_id,
                sku=meta["sku"],
                item_name=meta["name"],
                unit=meta["unit"] or "unit",
                qty_in=round(acc.qty_in, 4),
                qty_out=round(acc.qty_out, 4),
                on_hand_qty=round(acc.on_hand, 4),
                average_cost=acc.avg_cost,
                inventory_value=int(acc.inventory_value),
                cogs=int(acc.cogs),
            )
            out.append(row)
            total_value += row.inventory_value
            total_cogs += row.cogs
            total_qty += row.on_hand_qty
        out.sort(key=lambda r: r.item_name.lower())
        return InventoryBalanceResponse(
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            rows=out,
            totals={"inventory_value": total_value, "cogs": total_cogs, "on_hand_qty": round(total_qty, 4)},
        )
