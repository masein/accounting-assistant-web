from __future__ import annotations

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


class InventoryReportService:
    def __init__(self, db: Session):
        self.db = db

    def create_item(self, payload: InventoryItemCreate) -> InventoryItemRead:
        # list_price was accepted but never stored, so every new item read back 0.
        barcode = (payload.barcode or "").strip() or None
        self.assert_barcode_free(barcode)
        row = InventoryItem(sku=(payload.sku or "").strip() or None, name=payload.name.strip(),
                            unit=(payload.unit or "unit").strip(), list_price=int(payload.list_price or 0),
                            barcode=barcode, reorder_level=payload.reorder_level, reorder_qty=payload.reorder_qty)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return InventoryItemRead.model_validate(row)

    def assert_barcode_free(self, barcode: str | None, *, except_id=None) -> None:
        if not barcode:
            return
        from sqlalchemy import select
        clash = self.db.execute(select(InventoryItem.id).where(InventoryItem.barcode == barcode)).scalars().all()
        if any(i != except_id for i in clash):
            raise HTTPException(status_code=409, detail=f"Another item already has barcode {barcode}.")

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

    def balance_report(self, to_date: date | None = None, method: str | None = None) -> InventoryBalanceResponse:
        """Per item on hand, unit cost, value and cost of sales to date, costed
        with the company's method (weighted average unless set to FIFO)."""
        from app.services.inventory_costing import get_method, run_costing
        period = default_period(None, to_date)
        rows = inventory_movements_for_balance(self.db, period.to_date)
        item_map: dict[UUID, dict] = {item.id: {"name": item.name, "sku": item.sku, "unit": item.unit}
                                      for _mv, item in rows}
        states, _ = run_costing([mv for mv, _item in rows], method or get_method(self.db))

        out: list[InventoryBalanceRow] = []
        total_value = 0
        total_cogs = 0
        total_qty = 0.0
        for item_id, st in states.items():
            meta = item_map[item_id]
            row = InventoryBalanceRow(
                item_id=item_id,
                sku=meta["sku"],
                item_name=meta["name"],
                unit=meta["unit"] or "unit",
                qty_in=round(float(st.qty_in), 4),
                qty_out=round(float(st.qty_out), 4),
                on_hand_qty=round(float(st.on_hand), 4),
                average_cost=st.unit_cost,
                inventory_value=int(st.value),
                cogs=int(st.cogs),
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
