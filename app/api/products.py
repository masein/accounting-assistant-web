"""Products & Relationships hub — aggregates product-entity data from invoices."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.entity import Entity
from app.models.inventory import InventoryItem
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.schemas.product import (
    EntityMatrixResponse,
    EntityProductRow,
    EntityRelationshipRow,
    ProfitabilityResponse,
    ProfitByClientProduct,
    ProfitByProduct,
    ProductCatalogItem,
    ProductCatalogResponse,
    ProductClientRow,
    ProductDetailResponse,
    ProductInvoiceRow,
    ProductMonthlyRow,
    ProductSummaryRow,
    ProductSupplierRow,
)

router = APIRouter(prefix="/products", tags=["products"])


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _load_invoice_items(db: Session, from_date: date | None = None, to_date: date | None = None):
    """Load all invoice items with their invoice and entity, optionally filtered by date."""
    q = (
        select(InvoiceItem, Invoice, Entity)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .outerjoin(Entity, Invoice.entity_id == Entity.id)
    )
    if from_date:
        q = q.where(Invoice.issue_date >= from_date)
    if to_date:
        q = q.where(Invoice.issue_date <= to_date)
    return db.execute(q).all()


@router.get("/catalog", response_model=ProductCatalogResponse)
def product_catalog(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
) -> ProductCatalogResponse:
    """Unified product list with revenue/cost/profit aggregates."""
    rows = _load_invoice_items(db, from_date, to_date)

    # Build inventory lookup
    inv_items = {i.name.strip().lower(): i for i in db.execute(select(InventoryItem)).scalars().all()}

    products: dict[str, dict] = {}
    for item, invoice, entity in rows:
        key = _norm(item.product_name)
        if search and search.lower() not in key:
            continue
        if key not in products:
            inv = inv_items.get(key)
            products[key] = {
                "product_name": item.product_name.strip(),
                "inventory_item_id": inv.id if inv else (item.inventory_item_id or None),
                "sku": inv.sku if inv else None,
                "sales_revenue": 0,
                "purchase_cost": 0,
                "sales_count": 0,
                "purchase_count": 0,
                "clients": set(),
                "suppliers": set(),
            }
        p = products[key]
        if invoice.kind == "sales":
            p["sales_revenue"] += item.line_total or 0
            p["sales_count"] += 1
            if entity:
                p["clients"].add(entity.id)
        elif invoice.kind == "purchase":
            p["purchase_cost"] += item.line_total or 0
            p["purchase_count"] += 1
            if entity:
                p["suppliers"].add(entity.id)

    total_rev = total_cost = total_profit = 0
    items = []
    for p in sorted(products.values(), key=lambda x: x["sales_revenue"], reverse=True):
        profit = p["sales_revenue"] - p["purchase_cost"]
        margin = round(profit / p["sales_revenue"] * 100, 1) if p["sales_revenue"] else None
        total_rev += p["sales_revenue"]
        total_cost += p["purchase_cost"]
        total_profit += profit
        items.append(ProductCatalogItem(
            product_name=p["product_name"],
            inventory_item_id=p["inventory_item_id"],
            sku=p["sku"],
            total_sales_revenue=p["sales_revenue"],
            total_purchase_cost=p["purchase_cost"],
            gross_profit=profit,
            margin_pct=margin,
            sales_invoice_count=p["sales_count"],
            purchase_invoice_count=p["purchase_count"],
            client_count=len(p["clients"]),
            supplier_count=len(p["suppliers"]),
        ))
    return ProductCatalogResponse(items=items, total_revenue=total_rev, total_cost=total_cost, total_profit=total_profit)


@router.get("/detail/{product_name}", response_model=ProductDetailResponse)
def product_detail(
    product_name: str,
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> ProductDetailResponse:
    """Full detail for a single product."""
    rows = _load_invoice_items(db, from_date, to_date)
    key = _norm(product_name)

    inv_item = db.execute(
        select(InventoryItem).where(func.lower(func.trim(InventoryItem.name)) == key)
    ).scalars().first()

    clients: dict[str, dict] = {}
    suppliers: dict[str, dict] = {}
    invoices: list[ProductInvoiceRow] = []
    monthly: dict[str, dict] = defaultdict(lambda: {"revenue": 0, "cost": 0})
    total_rev = total_cost = 0

    for item, invoice, entity in rows:
        if _norm(item.product_name) != key:
            continue
        mo = _month_key(invoice.issue_date)
        entity_name = entity.name if entity else None

        invoices.append(ProductInvoiceRow(
            invoice_id=invoice.id,
            number=invoice.number,
            kind=invoice.kind,
            issue_date=invoice.issue_date,
            entity_name=entity_name,
            quantity=float(item.quantity or 0),
            unit_price=item.unit_price or 0,
            line_total=item.line_total or 0,
        ))

        if invoice.kind == "sales":
            total_rev += item.line_total or 0
            monthly[mo]["revenue"] += item.line_total or 0
            if entity:
                eid = str(entity.id)
                if eid not in clients:
                    clients[eid] = {"entity_id": entity.id, "name": entity.name, "revenue": 0, "count": 0}
                clients[eid]["revenue"] += item.line_total or 0
                clients[eid]["count"] += 1
        elif invoice.kind == "purchase":
            total_cost += item.line_total or 0
            monthly[mo]["cost"] += item.line_total or 0
            if entity:
                eid = str(entity.id)
                if eid not in suppliers:
                    suppliers[eid] = {"entity_id": entity.id, "name": entity.name, "cost": 0, "count": 0}
                suppliers[eid]["cost"] += item.line_total or 0
                suppliers[eid]["count"] += 1

    profit = total_rev - total_cost
    margin = round(profit / total_rev * 100, 1) if total_rev else None

    return ProductDetailResponse(
        product_name=product_name,
        sku=inv_item.sku if inv_item else None,
        inventory_item_id=inv_item.id if inv_item else None,
        total_revenue=total_rev,
        total_cost=total_cost,
        gross_profit=profit,
        margin_pct=margin,
        clients=[ProductClientRow(entity_id=c["entity_id"], name=c["name"], revenue=c["revenue"], invoice_count=c["count"]) for c in sorted(clients.values(), key=lambda x: x["revenue"], reverse=True)],
        suppliers=[ProductSupplierRow(entity_id=s["entity_id"], name=s["name"], cost=s["cost"], invoice_count=s["count"]) for s in sorted(suppliers.values(), key=lambda x: x["cost"], reverse=True)],
        invoices=sorted(invoices, key=lambda x: x.issue_date, reverse=True),
        monthly_series=[ProductMonthlyRow(month=m, revenue=d["revenue"], cost=d["cost"]) for m, d in sorted(monthly.items())],
    )


@router.get("/entity-matrix", response_model=EntityMatrixResponse)
def entity_matrix(
    entity_type: str | None = Query(None, description="client or supplier"),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    search: str | None = Query(None, description="Filter by entity or product name"),
    db: Session = Depends(get_db),
) -> EntityMatrixResponse:
    """Pivot: entity x product with revenue/cost."""
    rows = _load_invoice_items(db, from_date, to_date)

    entities: dict[str, dict] = {}
    product_agg: dict[str, dict] = defaultdict(lambda: {"revenue": 0, "cost": 0, "entities": set()})

    search_lower = search.lower().strip() if search else None

    for item, invoice, entity in rows:
        if not entity:
            continue
        if entity_type and entity.type != entity_type:
            continue
        # Filter by entity name or product name
        if search_lower:
            entity_match = search_lower in (entity.name or "").lower()
            product_match = search_lower in (item.product_name or "").lower()
            if not entity_match and not product_match:
                continue

        eid = str(entity.id)
        pname = item.product_name.strip()

        if eid not in entities:
            entities[eid] = {
                "entity_id": entity.id,
                "entity_name": entity.name,
                "entity_type": entity.type or "unknown",
                "products": {},
                "total_revenue": 0,
                "total_cost": 0,
            }
        e = entities[eid]
        if pname not in e["products"]:
            e["products"][pname] = {"revenue": 0, "cost": 0, "count": 0}
        p = e["products"][pname]

        if invoice.kind == "sales":
            amt = item.line_total or 0
            p["revenue"] += amt
            p["count"] += 1
            e["total_revenue"] += amt
            product_agg[pname]["revenue"] += amt
        elif invoice.kind == "purchase":
            amt = item.line_total or 0
            p["cost"] += amt
            p["count"] += 1
            e["total_cost"] += amt
            product_agg[pname]["cost"] += amt
        product_agg[pname]["entities"].add(eid)

    relationships = []
    for e in sorted(entities.values(), key=lambda x: x["total_revenue"] + x["total_cost"], reverse=True):
        products = [EntityProductRow(product_name=pn, revenue=pd["revenue"], cost=pd["cost"], invoice_count=pd["count"])
                    for pn, pd in sorted(e["products"].items(), key=lambda x: x[1]["revenue"] + x[1]["cost"], reverse=True)]
        relationships.append(EntityRelationshipRow(
            entity_id=e["entity_id"],
            entity_name=e["entity_name"],
            entity_type=e["entity_type"],
            products=products,
            total_revenue=e["total_revenue"],
            total_cost=e["total_cost"],
            profit=e["total_revenue"] - e["total_cost"],
        ))

    product_summary = [ProductSummaryRow(product_name=pn, total_revenue=pd["revenue"], total_cost=pd["cost"], entity_count=len(pd["entities"]))
                       for pn, pd in sorted(product_agg.items(), key=lambda x: x[1]["revenue"] + x[1]["cost"], reverse=True)]

    return EntityMatrixResponse(relationships=relationships, product_summary=product_summary)


@router.get("/profitability", response_model=ProfitabilityResponse)
def profitability(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: Session = Depends(get_db),
) -> ProfitabilityResponse:
    """Per-product and per-client-product profitability analysis."""
    rows = _load_invoice_items(db, from_date, to_date)

    # Per-product aggregation
    by_product: dict[str, dict] = {}
    # Per client-product
    by_cp: dict[str, dict] = {}
    # Track top client per product
    product_client_rev: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item, invoice, entity in rows:
        pname = item.product_name.strip()
        pkey = _norm(pname)
        if pkey not in by_product:
            by_product[pkey] = {"product_name": pname, "revenue": 0, "cost": 0}
        bp = by_product[pkey]

        client_name = entity.name if entity else "Unknown"
        cpkey = f"{pkey}||{_norm(client_name)}"
        if cpkey not in by_cp:
            by_cp[cpkey] = {"client_name": client_name, "product_name": pname, "revenue": 0, "cost": 0}
        cp = by_cp[cpkey]

        if invoice.kind == "sales":
            amt = item.line_total or 0
            bp["revenue"] += amt
            cp["revenue"] += amt
            if entity:
                product_client_rev[pkey][entity.name] += amt
        elif invoice.kind == "purchase":
            amt = item.line_total or 0
            bp["cost"] += amt
            cp["cost"] += amt

    products = []
    total_rev = total_cost = total_profit = 0
    for bp in sorted(by_product.values(), key=lambda x: x["revenue"], reverse=True):
        profit = bp["revenue"] - bp["cost"]
        margin = round(profit / bp["revenue"] * 100, 1) if bp["revenue"] else None
        top_client_map = product_client_rev.get(_norm(bp["product_name"]), {})
        top_client = max(top_client_map, key=top_client_map.get) if top_client_map else None
        total_rev += bp["revenue"]
        total_cost += bp["cost"]
        total_profit += profit
        products.append(ProfitByProduct(
            product_name=bp["product_name"],
            revenue=bp["revenue"],
            cost=bp["cost"],
            profit=profit,
            margin_pct=margin,
            top_client=top_client,
        ))

    client_products = []
    for cp in sorted(by_cp.values(), key=lambda x: x["revenue"], reverse=True):
        profit = cp["revenue"] - cp["cost"]
        client_products.append(ProfitByClientProduct(
            client_name=cp["client_name"],
            product_name=cp["product_name"],
            revenue=cp["revenue"],
            cost=cp["cost"],
            profit=profit,
        ))

    avg_margin = round(total_profit / total_rev * 100, 1) if total_rev else None

    return ProfitabilityResponse(
        by_product=products,
        by_client_product=client_products[:100],
        total_revenue=total_rev,
        total_cost=total_cost,
        total_profit=total_profit,
        avg_margin=avg_margin,
    )
