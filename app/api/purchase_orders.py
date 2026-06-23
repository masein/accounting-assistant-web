"""Purchase orders, goods receipts, and 3-way match.

A PO is a commitment — it never posts to the ledger. Goods receipts track
quantities only (no inventory/GRNI posting). The match endpoint compares the
PO, its receipts, and an existing purchase invoice (bill); when a discrepancy
exists the bill is NOT auto-approved — the caller must confirm explicitly.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entity import Entity
from app.models.goods_receipt import GoodsReceipt, GoodsReceiptLine
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.purchase_order import PurchaseOrder, PurchaseOrderLine
from app.services import three_way_match
from app.services.audit_service import log_audit_event
from app.services.fx_service import get_reporting_currency

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])

_date = date

_VALID_STATUS = {"draft", "issued", "partially_received", "received", "closed", "cancelled"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class POLineInput(BaseModel):
    description: str
    ordered_qty: float = Field(..., gt=0)
    unit_price: int = Field(..., ge=0)
    inventory_item_id: UUID | None = None


class POCreate(BaseModel):
    entity_id: UUID | None = None
    order_date: _date
    expected_date: _date | None = None
    currency: str | None = None
    description: str | None = None
    number: str | None = None
    status: str = "issued"
    lines: list[POLineInput]


class POPatch(BaseModel):
    status: str | None = None
    expected_date: _date | None = None
    description: str | None = None


class ReceiptLineInput(BaseModel):
    po_line_id: UUID
    quantity: float = Field(..., gt=0)


class ReceiptCreate(BaseModel):
    receipt_date: _date
    note: str | None = None
    lines: list[ReceiptLineInput]


class MatchRequest(BaseModel):
    invoice_id: UUID
    price_tolerance: float = Field(three_way_match.DEFAULT_PRICE_TOLERANCE, ge=0, le=1)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _line_read(ln: PurchaseOrderLine) -> dict:
    return {
        "id": str(ln.id),
        "inventory_item_id": str(ln.inventory_item_id) if ln.inventory_item_id else None,
        "description": ln.description,
        "ordered_qty": float(ln.ordered_qty or 0),
        "received_qty": float(ln.received_qty or 0),
        "unit_price": int(ln.unit_price or 0),
        "line_total": int(ln.line_total or 0),
    }


def _po_read(po: PurchaseOrder, db: Session) -> dict:
    name = None
    if po.entity_id:
        ent = db.get(Entity, po.entity_id)
        name = ent.name if ent else None
    return {
        "id": str(po.id),
        "number": po.number,
        "entity_id": str(po.entity_id) if po.entity_id else None,
        "supplier_name": name,
        "order_date": po.order_date.isoformat(),
        "expected_date": po.expected_date.isoformat() if po.expected_date else None,
        "status": po.status,
        "currency": po.currency,
        "description": po.description,
        "matched_invoice_id": str(po.matched_invoice_id) if po.matched_invoice_id else None,
        "total": sum(int(li.line_total or 0) for li in po.lines),
        "lines": [_line_read(li) for li in po.lines],
    }


def _recompute_status(po: PurchaseOrder) -> None:
    """Move the PO along the receipt lifecycle without overriding terminal
    states (closed/cancelled) or a draft that hasn't been issued."""
    if po.status in ("cancelled", "closed", "draft"):
        return
    total_ordered = sum(float(li.ordered_qty or 0) for li in po.lines)
    total_received = sum(float(li.received_qty or 0) for li in po.lines)
    if total_received <= 0:
        po.status = "issued"
    elif all(float(li.received_qty or 0) >= float(li.ordered_qty or 0) for li in po.lines):
        po.status = "received"
    else:
        po.status = "partially_received"


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
def create_po(payload: POCreate, db: Session = Depends(get_db)) -> dict:
    if not payload.lines:
        raise HTTPException(status_code=422, detail="A purchase order needs at least one line.")
    if payload.status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail=f"Invalid status '{payload.status}'.")
    if payload.entity_id is not None:
        ent = db.get(Entity, payload.entity_id)
        if not ent:
            raise HTTPException(status_code=404, detail="Supplier entity not found.")

    cur = (payload.currency or get_reporting_currency(db) or "IRR").upper()
    number = payload.number or f"PO-{(db.execute(select(func.count(PurchaseOrder.id))).scalar() or 0) + 1:05d}"
    po = PurchaseOrder(
        number=number, entity_id=payload.entity_id, order_date=payload.order_date,
        expected_date=payload.expected_date, status=payload.status, currency=cur,
        description=payload.description,
    )
    db.add(po)
    db.flush()
    for li in payload.lines:
        db.add(PurchaseOrderLine(
            order_id=po.id, inventory_item_id=li.inventory_item_id,
            description=li.description, ordered_qty=li.ordered_qty, received_qty=0,
            unit_price=int(li.unit_price), line_total=int(round(li.ordered_qty * li.unit_price)),
        ))
    log_audit_event(db, action="create", entity_type="purchase_order", entity_id=str(po.id),
                    detail=f"PO {number}")
    db.commit()
    db.refresh(po)
    return _po_read(po, db)


@router.get("")
def list_pos(db: Session = Depends(get_db)) -> list[dict]:
    pos = db.execute(select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())).scalars().all()
    return [_po_read(p, db) for p in pos]


@router.get("/{po_id}")
def get_po(po_id: UUID, db: Session = Depends(get_db)) -> dict:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    return _po_read(po, db)


@router.get("/{po_id}/pdf")
def purchase_order_pdf(po_id: UUID, db: Session = Depends(get_db)):
    """Branded purchase-order PDF (issuer → supplier). Tenant-scoped → 404 cross-company."""
    from fastapi.responses import Response
    from app.models.entity import Entity
    from app.services.documents import render_purchase_order_pdf
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    supplier = db.get(Entity, po.entity_id) if po.entity_id else None
    pdf = render_purchase_order_pdf(db, po, supplier)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="po-{po.number}.pdf"'},
    )


@router.patch("/{po_id}")
def patch_po(po_id: UUID, payload: POPatch, db: Session = Depends(get_db)) -> dict:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    if payload.status is not None:
        if payload.status not in _VALID_STATUS:
            raise HTTPException(status_code=422, detail=f"Invalid status '{payload.status}'.")
        po.status = payload.status
    if payload.expected_date is not None:
        po.expected_date = payload.expected_date
    if payload.description is not None:
        po.description = payload.description
    log_audit_event(db, action="update", entity_type="purchase_order", entity_id=str(po.id),
                    detail=f"PO {po.number} updated")
    db.commit()
    db.refresh(po)
    return _po_read(po, db)


# ---------------------------------------------------------------------------
# Goods receipts
# ---------------------------------------------------------------------------


@router.post("/{po_id}/receipts", status_code=201)
def record_receipt(po_id: UUID, payload: ReceiptCreate, db: Session = Depends(get_db)) -> dict:
    """Record quantities received against PO lines. Tracks quantities only —
    posts nothing to the ledger. Receiving more than ordered is rejected."""
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    if po.status in ("cancelled", "closed"):
        raise HTTPException(status_code=409, detail=f"PO is {po.status}; cannot receive against it.")
    if not payload.lines:
        raise HTTPException(status_code=422, detail="A receipt needs at least one line.")

    lines_by_id = {li.id: li for li in po.lines}
    receipt = GoodsReceipt(order_id=po.id, receipt_date=payload.receipt_date, note=payload.note)
    db.add(receipt)
    db.flush()
    for rl in payload.lines:
        po_line = lines_by_id.get(rl.po_line_id)
        if not po_line:
            raise HTTPException(status_code=422, detail=f"PO line {rl.po_line_id} not on this order.")
        new_received = float(po_line.received_qty or 0) + float(rl.quantity)
        if new_received > float(po_line.ordered_qty or 0) + 1e-9:
            raise HTTPException(
                status_code=422,
                detail=f"Receiving {rl.quantity} exceeds the outstanding ordered qty for '{po_line.description}'.",
            )
        po_line.received_qty = new_received
        db.add(GoodsReceiptLine(receipt_id=receipt.id, po_line_id=po_line.id, quantity=float(rl.quantity)))

    _recompute_status(po)
    log_audit_event(db, action="create", entity_type="goods_receipt", entity_id=str(receipt.id),
                    detail=f"Goods receipt for PO {po.number}")
    db.commit()
    db.refresh(po)
    return _po_read(po, db)


# ---------------------------------------------------------------------------
# 3-way match
# ---------------------------------------------------------------------------


@router.post("/{po_id}/match")
def match_po(po_id: UUID, payload: MatchRequest, db: Session = Depends(get_db)) -> dict:
    """Compare this PO + its receipts against a purchase invoice (bill). Returns
    matched / discrepancies. A clean match links the bill to the PO; a
    discrepancy is NOT auto-approved — the caller must confirm explicitly."""
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    inv = db.get(Invoice, payload.invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if inv.kind != "purchase":
        raise HTTPException(status_code=422, detail="3-way match applies to purchase invoices (bills).")

    po_lines = [
        {
            "key": str(li.inventory_item_id) if li.inventory_item_id else None,
            "description": li.description,
            "ordered_qty": float(li.ordered_qty or 0),
            "received_qty": float(li.received_qty or 0),
            "unit_price": int(li.unit_price or 0),
        }
        for li in po.lines
    ]
    items = db.execute(
        select(InvoiceItem).where(InvoiceItem.invoice_id == inv.id)
    ).scalars().all()
    invoice_lines = [
        {
            "key": str(it.inventory_item_id) if it.inventory_item_id else None,
            "description": it.product_name,
            "quantity": float(it.quantity or 0),
            "unit_price": int(it.unit_price or 0),
        }
        for it in items
    ]

    result = three_way_match.match(po_lines, invoice_lines, price_tolerance=payload.price_tolerance)

    # A clean match links the bill to the PO; a discrepancy never auto-approves.
    if result.matched:
        po.matched_invoice_id = inv.id
    log_audit_event(
        db, action="match", entity_type="purchase_order", entity_id=str(po.id),
        detail=f"3-way match PO {po.number} ↔ bill {inv.number}: "
               + ("matched" if result.matched else f"{len(result.discrepancies)} discrepancy(ies)"),
    )
    db.commit()

    out = result.to_dict()
    out["po_id"] = str(po.id)
    out["invoice_id"] = str(inv.id)
    out["invoice_number"] = inv.number
    return out
