"""Inventory costing (roadmap 2026-09 §4.4): weighted average or FIFO, stock
valuation as of a date, reorder alerts, and a light bill of materials with
production runs.

The movements are the record; value is always recomputed from them in date
order, so switching the method re-values history instead of freezing old
figures. The company's method is a setting (``inventory_costing``), default
weighted average — the figures the reports showed before.

* **Weighted average** — the running average the balance report has always
  used: an OUT with no cost of its own leaves at the average (now rounded
  half-up, never half-to-even).
* **FIFO** — every IN (and positive adjustment) is a layer; an OUT uses the
  oldest layers first. Selling more than is on hand leaves a shortfall costed
  at the last known unit cost; the next receipt fills it first.

Quantities are Decimal-exact to the stored 4 places; money is whole units,
rounded half-up per layer.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import InventoryBomLine, InventoryItem, InventoryMovement, InventoryMovementType

WEIGHTED_AVERAGE = "weighted_average"
FIFO = "fifo"
METHODS = (WEIGHTED_AVERAGE, FIFO)
SETTING_KEY = "inventory_costing"
IN, OUT, ADJ = (InventoryMovementType.IN.value, InventoryMovementType.OUT.value,
                InventoryMovementType.ADJUSTMENT.value)
Q = Decimal("0.0001")


def _money(v: Decimal) -> int:
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _qty(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Q)


# --- the setting ------------------------------------------------------------------------------------

def get_method(db: Session) -> str:
    from app.models.app_setting import AppSetting
    row = db.execute(select(AppSetting).where(AppSetting.key == SETTING_KEY)).scalar_one_or_none()
    try:
        method = json.loads(row.value).get("method") if row and row.value else None
    except (ValueError, AttributeError):
        method = None
    return method if method in METHODS else WEIGHTED_AVERAGE


def set_method(db: Session, method: str) -> str:
    from app.models.app_setting import AppSetting
    if method not in METHODS:
        raise HTTPException(status_code=422, detail=f"Costing method must be one of {', '.join(METHODS)}.")
    row = db.execute(select(AppSetting).where(AppSetting.key == SETTING_KEY)).scalar_one_or_none()
    value = json.dumps({"method": method})
    if row is None:
        db.add(AppSetting(key=SETTING_KEY, value=value))
    else:
        row.value = value
    db.flush()
    return method


# --- the engine ----------------------------------------------------------------------------------------

@dataclass
class ItemState:
    qty_in: Decimal = Decimal(0)
    qty_out: Decimal = Decimal(0)
    on_hand: Decimal = Decimal(0)
    value: int = 0
    cogs: int = 0
    last_cost: int = 0
    shortfall: Decimal = Decimal(0)                     # FIFO: sold but never received
    layers: deque = field(default_factory=deque)         # FIFO: [qty, unit_cost]

    @property
    def unit_cost(self) -> int:
        return _money(Decimal(self.value) / self.on_hand) if self.on_hand > 0 else 0


def _apply_weighted(st: ItemState, typ: str, qty: Decimal, cost: int) -> int:
    """The running average the balance report always used; returns this movement's cost."""
    if typ == OUT:
        st.qty_out += qty
        use = cost if cost > 0 else st.unit_cost
        cogs = _money(qty * use)
        st.cogs += cogs
        st.on_hand -= qty
        st.value = max(0, st.value - cogs) if st.on_hand > 0 else 0
        return cogs
    st.qty_in += qty
    st.on_hand += qty
    added = _money(qty * cost)
    st.value += added
    st.last_cost = cost or st.last_cost
    return added


def _apply_fifo(st: ItemState, typ: str, qty: Decimal, cost: int) -> int:
    if typ == OUT:
        st.qty_out += qty
        need, cogs = qty, 0
        while need > 0 and st.layers:
            layer = st.layers[0]
            take = min(need, layer[0])
            cogs += _money(take * layer[1])
            layer[0] -= take
            need -= take
            if layer[0] <= 0:
                st.layers.popleft()
        if need > 0:                                     # oversold: at the last cost known
            cogs += _money(need * st.last_cost)
            st.shortfall += need
        st.cogs += cogs
        st.on_hand -= qty
        st.value = sum(_money(q * c) for q, c in st.layers)
        return cogs
    st.qty_in += qty
    st.on_hand += qty
    st.last_cost = cost or st.last_cost
    remaining = qty
    if st.shortfall > 0:                                 # a receipt first covers what was oversold
        covered = min(st.shortfall, remaining)
        st.shortfall -= covered
        remaining -= covered
    if remaining > 0:
        st.layers.append([remaining, cost])
    st.value = sum(_money(q * c) for q, c in st.layers)
    return _money(qty * cost)


def run_costing(movements, method: str) -> tuple[dict, dict]:
    """(item id → ItemState, movement id → cost) for movements in date order."""
    states: dict = defaultdict(ItemState)
    costs: dict = {}
    apply = _apply_fifo if method == FIFO else _apply_weighted
    for mv in movements:
        qty = _qty(mv.quantity)
        if qty <= 0:
            continue
        typ = mv.movement_type.value if hasattr(mv.movement_type, "value") else str(mv.movement_type)
        costs[mv.id] = apply(states[mv.item_id], typ, qty, int(mv.unit_cost or 0))
    return states, costs


def _movements(db: Session, to_date: date | None = None, item_ids=None):
    q = select(InventoryMovement).order_by(InventoryMovement.movement_date, InventoryMovement.created_at,
                                           InventoryMovement.id)
    if to_date is not None:
        q = q.where(InventoryMovement.movement_date <= to_date)
    if item_ids is not None:
        q = q.where(InventoryMovement.item_id.in_(list(item_ids)))
    return db.execute(q).scalars().all()


# --- valuation and reorder ------------------------------------------------------------------------------------

def _f(v: Decimal) -> float:
    return float(v.quantize(Q))


def valuation(db: Session, *, as_of: date | None = None, method: str | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    method = method or get_method(db)
    if method not in METHODS:
        raise HTTPException(status_code=422, detail=f"Costing method must be one of {', '.join(METHODS)}.")
    movements = _movements(db, as_of)
    states, _ = run_costing(movements, method)
    other = FIFO if method == WEIGHTED_AVERAGE else WEIGHTED_AVERAGE
    other_states, _ = run_costing(movements, other)
    items = db.execute(select(InventoryItem).order_by(InventoryItem.name)).scalars().all()
    rows = []
    for it in items:
        st = states.get(it.id)
        if st is None and not it.is_active:
            continue
        st = st or ItemState()
        level = _qty(it.reorder_level) if it.reorder_level is not None else None
        rows.append({
            "item_id": str(it.id), "name": it.name, "sku": it.sku, "barcode": it.barcode, "unit": it.unit,
            "is_active": it.is_active, "qty_in": _f(st.qty_in), "qty_out": _f(st.qty_out),
            "on_hand": _f(st.on_hand), "unit_cost": st.unit_cost, "value": st.value, "cogs": st.cogs,
            "list_price": int(it.list_price or 0),
            "reorder_level": _f(level) if level is not None else None,
            "reorder_qty": _f(_qty(it.reorder_qty)) if it.reorder_qty is not None else None,
            "below_reorder": level is not None and it.is_active and st.on_hand <= level,
            "oversold": _f(-st.on_hand) if st.on_hand < 0 else 0.0,
        })
    return {
        "as_of": as_of.isoformat(), "method": method, "rows": rows,
        "totals": {"value": sum(r["value"] for r in rows), "cogs": sum(r["cogs"] for r in rows),
                   "items": len(rows), "below_reorder": sum(1 for r in rows if r["below_reorder"])},
        "other_method": {"method": other, "value": sum(s.value for s in other_states.values()),
                         "cogs": sum(s.cogs for s in other_states.values())},
    }


def low_stock(db: Session, *, as_of: date | None = None) -> list[dict[str, Any]]:
    rows = [r for r in valuation(db, as_of=as_of)["rows"] if r["below_reorder"]]
    for r in rows:
        r["suggested_order"] = r["reorder_qty"] if r["reorder_qty"] else max(0.0, (r["reorder_level"] or 0) - r["on_hand"])
    return sorted(rows, key=lambda r: (r["on_hand"] - (r["reorder_level"] or 0), r["name"]))


def unit_cost_on(db: Session, item_id, on: date) -> int:
    states, _ = run_costing(_movements(db, on, [item_id]), get_method(db))
    st = states.get(item_id)
    return st.unit_cost if st else 0


def movement_costs(db: Session, *, to_date: date | None = None) -> dict:
    """movement id → its cost under the company's method (the value an OUT left at)."""
    return run_costing(_movements(db, to_date), get_method(db))[1]


# --- bill of materials and production ---------------------------------------------------------------------------

def get_bom(db: Session, product_id) -> list[dict[str, Any]]:
    rows = db.execute(select(InventoryBomLine, InventoryItem)
                      .join(InventoryItem, InventoryItem.id == InventoryBomLine.component_id)
                      .where(InventoryBomLine.product_id == product_id)
                      .order_by(InventoryItem.name)).all()
    return [{"component_id": str(line.component_id), "name": it.name, "unit": it.unit,
             "quantity": _f(_qty(line.quantity))} for line, it in rows]


def set_bom(db: Session, product: InventoryItem, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the product's components: [{component_id, quantity}]."""
    seen = set()
    for ln in lines:
        cid = uuid.UUID(str(ln["component_id"]))
        if cid == product.id:
            raise HTTPException(status_code=422, detail="An item can't be a component of itself.")
        if cid in seen:
            raise HTTPException(status_code=422, detail="Each component can appear once.")
        if _qty(ln["quantity"]) <= 0:
            raise HTTPException(status_code=422, detail="Component quantities must be above zero.")
        if db.get(InventoryItem, cid) is None:
            raise HTTPException(status_code=404, detail=f"Component not found: {cid}")
        uses = {b.component_id for b in db.execute(select(InventoryBomLine)
                                                  .where(InventoryBomLine.product_id == cid)).scalars()}
        if product.id in uses:
            raise HTTPException(status_code=422, detail="A component can't itself be made from this item.")
        seen.add(cid)
    for old in db.execute(select(InventoryBomLine).where(InventoryBomLine.product_id == product.id)).scalars():
        db.delete(old)
    db.flush()
    for ln in lines:
        db.add(InventoryBomLine(product_id=product.id, component_id=uuid.UUID(str(ln["component_id"])),
                                quantity=float(_qty(ln["quantity"]))))
    db.flush()
    return get_bom(db, product.id)


def produce(db: Session, product: InventoryItem, quantity, *, on: date, reference: str | None = None,
            allow_short: bool = False) -> dict[str, Any]:
    """A production run: each component leaves at its cost (the company's
    method, as of ``on``), and the finished item comes in at their total."""
    qty = _qty(quantity)
    if qty <= 0:
        raise HTTPException(status_code=422, detail="Produce a quantity above zero.")
    if on > date.today():
        raise HTTPException(status_code=422, detail="A production run can't be dated in the future.")
    bom = db.execute(select(InventoryBomLine).where(InventoryBomLine.product_id == product.id)).scalars().all()
    if not bom:
        raise HTTPException(status_code=422, detail="This item has no bill of materials.")
    method = get_method(db)
    comp_ids = [b.component_id for b in bom]
    states, _ = run_costing(_movements(db, on, comp_ids), method)
    needs, short = [], []
    for b in bom:
        need = (_qty(b.quantity) * qty).quantize(Q)
        have = states.get(b.component_id, ItemState()).on_hand
        needs.append((b.component_id, need))
        if have < need:
            short.append({"component_id": str(b.component_id), "needed": _f(need), "on_hand": _f(have)})
    if short and not allow_short:
        raise HTTPException(status_code=409, detail={"message": "Not enough components on hand.", "short": short})
    ref = (reference or f"PROD-{product.sku or product.name}"[:120]).strip()
    # Cost each component the way the engine would, by replaying it with the OUT appended.
    total = 0
    out_rows = []
    for cid, need in needs:
        probe = InventoryMovement(id=uuid.uuid4(), item_id=cid, movement_date=on,
                                  movement_type=InventoryMovementType.OUT, quantity=float(need), unit_cost=0)
        _, costs = run_costing([*_movements(db, on, [cid]), probe], method)
        cost = costs.get(probe.id, 0)
        # cost 0: the engine values the component when it leaves, under either method
        out_rows.append(InventoryMovement(item_id=cid, movement_date=on, movement_type=InventoryMovementType.OUT,
                                          quantity=float(need), unit_cost=0, reference=ref,
                                          description=f"Used to make {_f(qty)} × {product.name}"))
        total += cost
    unit_cost = _money(Decimal(total) / qty)
    db.add_all(out_rows)
    db.add(InventoryMovement(item_id=product.id, movement_date=on, movement_type=InventoryMovementType.IN,
                             quantity=float(qty), unit_cost=unit_cost, reference=ref,
                             description=f"Produced from {len(bom)} component(s)"))
    db.flush()
    return {"product_id": str(product.id), "quantity": _f(qty), "on": on.isoformat(), "reference": ref,
            "components_cost": total, "unit_cost": unit_cost, "short": short, "method": method}


# --- insight: low stock -------------------------------------------------------------------------------------------

def detect_low_stock(db: Session, today: date) -> list:
    from app.services.insight_service import Insight
    rows = low_stock(db, as_of=today)
    if not rows:
        return []
    names = ", ".join(r["name"] for r in rows[:3]) + ("…" if len(rows) > 3 else "")
    return [Insight(
        key=f"low-stock-{today.isoformat()}", kind="low_stock", severity="warning" if len(rows) > 1 else "info",
        page="inventory", params={"count": len(rows), "names": names},
        data={"items": [{k: r[k] for k in ("name", "sku", "on_hand", "reorder_level", "suggested_order")}
                        for r in rows[:10]]},
    )]


DETECTORS = (("low_stock", detect_low_stock),)

from app.services.insight_service import _TEMPLATES  # noqa: E402 — wording lives with the detector

_TEMPLATES.update({
    "low_stock": {
        "title": {
            "en": "{count} item(s) at or below the reorder point",
            "fa": "{count} کالا به نقطهٔ سفارش یا کمتر رسیده است",
            "es": "{count} artículo(s) en el punto de pedido o por debajo",
            "ar": "{count} صنف/أصناف عند نقطة إعادة الطلب أو أقل",
        },
        "message": {
            "en": "Time to reorder: {names}.",
            "fa": "وقت سفارش مجدد است: {names}.",
            "es": "Es momento de volver a pedir: {names}.",
            "ar": "حان وقت إعادة الطلب: {names}.",
        },
    },
})
