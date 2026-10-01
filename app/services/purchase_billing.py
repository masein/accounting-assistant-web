"""Purchase orders to bills, their lifecycle, and supplier prices (roadmap §4.8).

* A PO moves draft → issued → (partially_) received by its receipts; a person
  may issue a draft, cancel an order nothing has arrived on, or close one
  (short-closing a partial delivery). Closed and cancelled are final; only a
  draft is deleted — an issued order stays on record.
* Received goods are billed from the order: a purchase invoice through the
  canonical invoice path (it recognises the payable), for what has arrived and
  isn't billed yet — all of it, or chosen quantities. Each bill line remembers
  its PO line; voiding the bill gives the quantities back.
* Price history: what an item (or a description) has cost, from which
  supplier, on orders and bills — newest first, with the last, lowest and
  highest price per currency.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.purchase_order import PurchaseOrder, PurchaseOrderLine

# who may set which status by hand (receipts set the received ones)
MANUAL_TRANSITIONS = {
    "draft": {"issued", "cancelled"},
    "issued": {"cancelled", "closed"},
    "partially_received": {"closed"},
    "received": {"closed"},
}
FINAL = {"closed", "cancelled"}
_EPS = Decimal("0.00005")


class POError(ValueError):
    """``status`` is the HTTP status the refusal maps to."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _q(x) -> Decimal:
    return Decimal(str(x or 0))


def check_transition(po: PurchaseOrder, target: str) -> None:
    if target == po.status:
        return
    if po.status in FINAL:
        raise POError(f"This order is {po.status}; it can't change any more.", 409)
    if target in ("partially_received", "received"):
        raise POError("Received states follow the goods receipts; record a receipt instead.", 422)
    if target == "cancelled" and any(_q(li.received_qty) > 0 for li in po.lines):
        raise POError("Goods have arrived on this order — close it instead of cancelling it.", 409)
    if target not in MANUAL_TRANSITIONS.get(po.status, set()):
        raise POError(f"A {po.status.replace('_', ' ')} order can't be set to {target}.", 422)


def billable(li: PurchaseOrderLine) -> Decimal:
    return max(Decimal(0), _q(li.received_qty) - _q(li.billed_qty))


def billing_status(po: PurchaseOrder) -> str:
    billed = sum((_q(li.billed_qty) for li in po.lines), Decimal(0))
    if billed <= 0:
        return "not_billed"
    ordered_or_received = [(_q(li.ordered_qty) if po.status != "closed" else _q(li.received_qty)) for li in po.lines]
    if all(_q(li.billed_qty) >= target - _EPS for li, target in zip(po.lines, ordered_or_received)):
        return "billed"
    return "partially_billed"


def bill_from_po(db: Session, po: PurchaseOrder, *, number: str | None = None, issue_date: date | None = None,
                 due_date: date | None = None, lines: list[dict] | None = None,
                 tax_rate: float = 0.0) -> Invoice:
    """A purchase invoice for received, not yet billed quantities. ``lines``
    is ``[{po_line_id, quantity}]``; omitted → everything billable. Flushed,
    not committed."""
    from app.api.invoices import insert_invoice, suggest_number
    from app.schemas.invoice import InvoiceCreate, InvoiceItemCreate

    if po.status in ("draft", "cancelled"):
        raise POError(f"A {po.status} order has nothing to bill.", 409)
    if po.entity_id is None:
        raise POError("The order has no supplier to bill from.", 422)
    by_id = {str(li.id): li for li in po.lines}
    wanted: list[tuple[PurchaseOrderLine, Decimal]] = []
    if lines:
        for item in lines:
            li = by_id.get(str(item.get("po_line_id")))
            if li is None:
                raise POError(f"Line {item.get('po_line_id')} isn't on this order.")
            qty = _q(item.get("quantity"))
            if qty <= 0:
                continue
            if qty > billable(li) + _EPS:
                raise POError(f"'{li.description}': {qty.normalize()} is more than has arrived and "
                              f"isn't billed yet ({billable(li).normalize()}).")
            wanted.append((li, qty))
    else:
        wanted = [(li, billable(li)) for li in po.lines if billable(li) > 0]
    if not wanted:
        raise POError("Nothing on this order has arrived that isn't billed already.")
    issue = issue_date or date.today()
    payload = InvoiceCreate(
        number=(number or "").strip() or suggest_number(db, Invoice, "BILL-", kind="purchase"),
        kind="purchase", status="issued", issue_date=issue, due_date=due_date or issue + timedelta(days=30),
        amount=0, currency=po.currency, entity_id=po.entity_id,
        description=f"Purchase order {po.number}" + (f" — {po.description}" if po.description else ""),
        items=[InvoiceItemCreate(product_name=li.description[:256], quantity=float(qty), unit_price=int(li.unit_price or 0),
                                 inventory_item_id=li.inventory_item_id, tax_rate=float(tax_rate or 0))
               for li, qty in wanted],
    )
    inv = insert_invoice(db, payload)
    inv.purchase_order_id = po.id
    for item, (li, qty) in zip(inv.items, wanted):          # the items are in the payload's order
        item.po_line_id = li.id
        li.billed_qty = float(_q(li.billed_qty) + qty)
    db.flush()
    return inv


def unbill_for_invoice(db: Session, invoice_id) -> int:
    """A voided bill gives its quantities back to the order lines it came from."""
    n = 0
    for item in db.execute(select(InvoiceItem).where(InvoiceItem.invoice_id == invoice_id,
                                                     InvoiceItem.po_line_id.is_not(None))).scalars():
        li = db.get(PurchaseOrderLine, item.po_line_id)
        if li is not None:
            li.billed_qty = float(max(Decimal(0), _q(li.billed_qty) - _q(item.quantity)))
            n += 1
    return n


def bills_for(db: Session, po: PurchaseOrder) -> list[dict]:
    rows = db.execute(select(Invoice).where(Invoice.purchase_order_id == po.id)
                      .order_by(Invoice.issue_date, Invoice.number)).scalars().all()
    return [{"id": str(i.id), "number": i.number, "issue_date": i.issue_date.isoformat(), "amount": int(i.amount or 0),
             "currency": i.currency, "status": i.status} for i in rows]


# --- supplier prices ------------------------------------------------------------------------------------------

def price_history(db: Session, *, inventory_item_id=None, q: str | None = None, entity_id=None,
                  limit: int = 50) -> dict[str, Any]:
    """What an item — or anything described like ``q`` — has cost."""
    if inventory_item_id is None and not (q or "").strip():
        raise POError("Give an item or some words of the description.")
    names = dict(db.execute(select(Entity.id, Entity.name)).all())
    rows: list[dict] = []
    po_cond = [PurchaseOrder.status != "cancelled"]
    bill_cond = [Invoice.kind == "purchase", Invoice.status.notin_(("voided", "draft", "canceled"))]
    if inventory_item_id is not None:
        po_cond.append(PurchaseOrderLine.inventory_item_id == inventory_item_id)
        bill_cond.append(InvoiceItem.inventory_item_id == inventory_item_id)
    else:
        from app.utils.text import fold_fa, fold_sql
        like = f"%{fold_fa(q.strip())}%"
        po_cond.append(fold_sql(PurchaseOrderLine.description).ilike(like))
        bill_cond.append(or_(fold_sql(InvoiceItem.product_name).ilike(like), fold_sql(InvoiceItem.description).ilike(like)))
    if entity_id is not None:
        po_cond.append(PurchaseOrder.entity_id == entity_id)
        bill_cond.append(Invoice.entity_id == entity_id)
    for li, po in db.execute(select(PurchaseOrderLine, PurchaseOrder)
                             .join(PurchaseOrder, PurchaseOrderLine.order_id == PurchaseOrder.id)
                             .where(and_(*po_cond)).order_by(PurchaseOrder.order_date.desc()).limit(limit)).all():
        rows.append({"date": po.order_date.isoformat(), "source": "order", "number": po.number,
                     "supplier": names.get(po.entity_id), "supplier_id": str(po.entity_id) if po.entity_id else None,
                     "description": li.description, "quantity": float(li.ordered_qty or 0),
                     "unit_price": int(li.unit_price or 0), "currency": po.currency})
    for it, inv in db.execute(select(InvoiceItem, Invoice).join(Invoice, InvoiceItem.invoice_id == Invoice.id)
                              .where(and_(*bill_cond), InvoiceItem.po_line_id.is_(None))   # a bill from a PO repeats its price
                              .order_by(Invoice.issue_date.desc()).limit(limit)).all():
        rows.append({"date": inv.issue_date.isoformat(), "source": "bill", "number": inv.number,
                     "supplier": names.get(inv.entity_id), "supplier_id": str(inv.entity_id) if inv.entity_id else None,
                     "description": it.product_name, "quantity": float(it.quantity or 0),
                     "unit_price": int(it.unit_price or 0), "currency": inv.currency})
    rows.sort(key=lambda r: (r["date"], r["source"] == "bill"), reverse=True)
    rows = rows[:limit]
    summary: dict[str, dict] = {}
    for r in rows:                                          # newest first → the first seen is the last price
        s = summary.setdefault(r["currency"], {"last": r["unit_price"], "last_date": r["date"], "last_supplier": r["supplier"],
                                               "lowest": r["unit_price"], "lowest_supplier": r["supplier"],
                                               "highest": r["unit_price"], "count": 0})
        s["count"] += 1
        if r["unit_price"] < s["lowest"]:
            s["lowest"], s["lowest_supplier"] = r["unit_price"], r["supplier"]
        s["highest"] = max(s["highest"], r["unit_price"])
    return {"rows": rows, "by_currency": summary}
