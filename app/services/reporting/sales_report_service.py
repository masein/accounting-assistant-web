from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.schemas.manager_report import (
    ReportPeriod,
    SalesByInvoiceRow,
    SalesByProductRow,
    SalesPurchaseReportResponse,
)
from app.services.reporting.common import default_period
from app.services.reporting.repository import invoices_between, purchase_items_between, sales_items_between


class SalesReportService:
    def __init__(self, db: Session):
        self.db = db

    def sales_by_product(self, from_date: date | None, to_date: date | None) -> SalesPurchaseReportResponse:
        period = default_period(from_date, to_date)
        rows = sales_items_between(self.db, period.from_date, period.to_date)
        by_product: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"quantity": 0.0, "sales_amount": 0, "estimated_cost": 0}
        )
        for item, _inv in rows:
            key = (item.product_name or "Unspecified").strip() or "Unspecified"
            qty = float(item.quantity or 0)
            sales = int(item.line_total or 0)
            unit_cost = int(item.unit_cost or 0)
            by_product[key]["quantity"] += qty
            by_product[key]["sales_amount"] += sales
            by_product[key]["estimated_cost"] += int(round(qty * unit_cost))

        out: list[SalesByProductRow] = []
        total_sales = total_cost = 0
        for name, vals in by_product.items():
            sales = int(vals["sales_amount"])
            cost = int(vals["estimated_cost"])
            profit = sales - cost
            margin = (profit / sales * 100.0) if sales > 0 else None
            out.append(
                SalesByProductRow(
                    product_name=name,
                    quantity=round(float(vals["quantity"]), 4),
                    sales_amount=sales,
                    estimated_cost=cost,
                    profit=profit,
                    margin_pct=(round(margin, 2) if margin is not None else None),
                )
            )
            total_sales += sales
            total_cost += cost
        out.sort(key=lambda x: x.sales_amount, reverse=True)
        total_profit = total_sales - total_cost
        return SalesPurchaseReportResponse(
            report_type="sales_by_product",
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            rows=out,
            totals={
                "sales_amount": total_sales,
                "estimated_cost": total_cost,
                "profit": total_profit,
                "margin_pct": round((total_profit / total_sales) * 100.0, 2) if total_sales > 0 else 0,
            },
        )

    def purchase_by_product(self, from_date: date | None, to_date: date | None) -> SalesPurchaseReportResponse:
        period = default_period(from_date, to_date)
        rows = purchase_items_between(self.db, period.from_date, period.to_date)
        by_product: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"quantity": 0.0, "amount": 0}
        )
        for item, _inv in rows:
            key = (item.product_name or "Unspecified").strip() or "Unspecified"
            qty = float(item.quantity or 0)
            amt = int(item.line_total or 0)
            by_product[key]["quantity"] += qty
            by_product[key]["amount"] += amt
        out: list[SalesByProductRow] = []
        total_amount = 0
        for name, vals in by_product.items():
            amount = int(vals["amount"])
            total_amount += amount
            out.append(
                SalesByProductRow(
                    product_name=name,
                    quantity=round(float(vals["quantity"]), 4),
                    sales_amount=amount,
                    estimated_cost=0,
                    profit=0,
                    margin_pct=None,
                )
            )
        out.sort(key=lambda x: x.sales_amount, reverse=True)
        return SalesPurchaseReportResponse(
            report_type="purchase_by_product",
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            rows=out,
            totals={"purchase_amount": total_amount},
        )

    def sales_by_invoice(self, from_date: date | None, to_date: date | None) -> SalesPurchaseReportResponse:
        return self._invoice_rows(kind="sales", from_date=from_date, to_date=to_date)

    def purchase_by_invoice(self, from_date: date | None, to_date: date | None) -> SalesPurchaseReportResponse:
        return self._invoice_rows(kind="purchase", from_date=from_date, to_date=to_date)

    def _invoice_rows(self, kind: str, from_date: date | None, to_date: date | None) -> SalesPurchaseReportResponse:
        period = default_period(from_date, to_date)
        invoices = invoices_between(self.db, period.from_date, period.to_date, kind=kind)
        entity_ids = [inv.entity_id for inv in invoices if inv.entity_id]
        entities = {}
        if entity_ids:
            ents = self.db.execute(select(Entity).where(Entity.id.in_(entity_ids))).scalars().all()
            entities = {e.id: e for e in ents}
        rows: list[SalesByInvoiceRow] = []
        total = 0
        for inv in invoices:
            total += int(inv.amount or 0)
            rows.append(
                SalesByInvoiceRow(
                    invoice_id=inv.id,
                    invoice_number=inv.number,
                    issue_date=inv.issue_date,
                    due_date=inv.due_date,
                    status=inv.status,
                    entity_name=(entities.get(inv.entity_id).name if inv.entity_id and entities.get(inv.entity_id) else None),
                    amount=int(inv.amount or 0),
                )
            )
        return SalesPurchaseReportResponse(
            report_type=f"{kind}_by_invoice",
            period=ReportPeriod(from_date=period.from_date, to_date=period.to_date),
            rows=rows,
            totals={f"{kind}_amount": total, "count": len(rows)},
        )
