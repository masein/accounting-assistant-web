"""Purchase orders & 3-way match (test plan §4.2, §4.3).

Match logic is unit-tested pure; the API is driven directly against an isolated
in-memory chart (UK + Iran). POs and goods receipts must post NOTHING to the
ledger — only the existing bill recognition does.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.purchase_orders import (
    MatchRequest,
    POCreate,
    POLineInput,
    ReceiptCreate,
    ReceiptLineInput,
    create_po,
    match_po,
    record_receipt,
)
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.transaction import Transaction
from app.services import three_way_match
from app.services.locale_service import set_reporting_locale


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _supplier(db: Session, name="Acme Supplies") -> Entity:
    e = Entity(type="supplier", name=name)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _bill(db: Session, items: list[tuple[str, float, int]]) -> Invoice:
    """A minimal purchase invoice (bill) with line items (name, qty, unit_price)."""
    inv = Invoice(number="BILL-1", kind="purchase", status="issued",
                  issue_date=date(2025, 6, 10), due_date=date(2025, 7, 10), currency="GBP")
    db.add(inv)
    db.flush()
    for name, qty, price in items:
        db.add(InvoiceItem(invoice_id=inv.id, product_name=name, quantity=qty,
                           unit_price=price, line_total=int(round(qty * price))))
    db.commit()
    db.refresh(inv)
    return inv


# ─── 1. Pure match logic (§4.3) ────────────────────────────────────────

_PO = [{"key": None, "description": "Widget", "ordered_qty": 10, "received_qty": 10, "unit_price": 100}]


def test_match_clean():
    r = three_way_match.match(_PO, [{"key": None, "description": "Widget", "quantity": 10, "unit_price": 100}])
    assert r.matched is True and r.discrepancies == []


def test_match_over_quantity():
    r = three_way_match.match(_PO, [{"description": "Widget", "quantity": 12, "unit_price": 100}])
    assert r.matched is False
    assert any(d["type"] == "over_quantity" for d in r.discrepancies)


def test_match_over_price():
    r = three_way_match.match(_PO, [{"description": "Widget", "quantity": 10, "unit_price": 130}])
    assert r.matched is False
    assert any(d["type"] == "over_price" for d in r.discrepancies)


def test_match_short_receipt():
    po = [{"description": "Widget", "ordered_qty": 10, "received_qty": 4, "unit_price": 100}]
    r = three_way_match.match(po, [{"description": "Widget", "quantity": 8, "unit_price": 100}])
    assert r.matched is False
    assert any(d["type"] == "short_receipt" for d in r.discrepancies)


def test_match_price_within_tolerance_ok():
    # 1% tolerance: 100 → up to 101 is fine.
    r = three_way_match.match(_PO, [{"description": "Widget", "quantity": 10, "unit_price": 101}])
    assert r.matched is True


def test_match_no_po_line():
    r = three_way_match.match(_PO, [{"description": "Mystery", "quantity": 1, "unit_price": 5}])
    assert r.matched is False
    assert any(d["type"] == "no_po_line" for d in r.discrepancies)


# ─── 2. End-to-end on UK + Iran (§4.2, §4.3) ───────────────────────────

@pytest.mark.parametrize("fixture", ["uk", "ir"])
def test_clean_three_way_match_end_to_end(fixture, request):
    db = request.getfixturevalue(fixture)
    sup = _supplier(db)
    po = create_po(POCreate(
        entity_id=sup.id, order_date=date(2025, 6, 1),
        lines=[POLineInput(description="Widget", ordered_qty=10, unit_price=100)],
    ), db)
    assert po["status"] == "issued" and po["total"] == 1000
    po_line_id = UUID(po["lines"][0]["id"])

    # No ledger postings from creating a PO.
    assert db.execute(select(func.count(Transaction.id))).scalar() == 0

    recv = record_receipt(UUID(po["id"]), ReceiptCreate(
        receipt_date=date(2025, 6, 5),
        lines=[ReceiptLineInput(po_line_id=po_line_id, quantity=10)],
    ), db)
    assert recv["status"] == "received"
    assert recv["lines"][0]["received_qty"] == 10
    # Receipts post nothing either.
    assert db.execute(select(func.count(Transaction.id))).scalar() == 0

    bill = _bill(db, [("Widget", 10, 100)])
    res = match_po(UUID(po["id"]), MatchRequest(invoice_id=bill.id), db)
    assert res["matched"] is True
    assert res["discrepancies"] == []
    # Clean match links the bill to the PO.
    from app.models.purchase_order import PurchaseOrder
    assert str(db.get(PurchaseOrder, UUID(po["id"])).matched_invoice_id) == str(bill.id)


@pytest.mark.parametrize("fixture", ["uk", "ir"])
def test_bill_over_po_flags_and_not_approved(fixture, request):
    db = request.getfixturevalue(fixture)
    sup = _supplier(db)
    po = create_po(POCreate(
        entity_id=sup.id, order_date=date(2025, 6, 1),
        lines=[POLineInput(description="Widget", ordered_qty=10, unit_price=100)],
    ), db)
    po_line_id = UUID(po["lines"][0]["id"])
    record_receipt(UUID(po["id"]), ReceiptCreate(
        receipt_date=date(2025, 6, 5),
        lines=[ReceiptLineInput(po_line_id=po_line_id, quantity=10)],
    ), db)
    # Bill has more qty AND a higher price than the PO.
    bill = _bill(db, [("Widget", 12, 130)])
    res = match_po(UUID(po["id"]), MatchRequest(invoice_id=bill.id), db)
    assert res["matched"] is False
    kinds = {d["type"] for d in res["discrepancies"]}
    assert "over_quantity" in kinds and "over_price" in kinds
    # Not auto-approved → no link recorded.
    from app.models.purchase_order import PurchaseOrder
    assert db.get(PurchaseOrder, UUID(po["id"])).matched_invoice_id is None


def test_short_receipt_flagged(uk):
    sup = _supplier(uk)
    po = create_po(POCreate(
        entity_id=sup.id, order_date=date(2025, 6, 1),
        lines=[POLineInput(description="Widget", ordered_qty=10, unit_price=100)],
    ), uk)
    po_line_id = UUID(po["lines"][0]["id"])
    # Receive only 4, bill for 8.
    recv = record_receipt(UUID(po["id"]), ReceiptCreate(
        receipt_date=date(2025, 6, 5),
        lines=[ReceiptLineInput(po_line_id=po_line_id, quantity=4)],
    ), uk)
    assert recv["status"] == "partially_received"
    bill = _bill(uk, [("Widget", 8, 100)])
    res = match_po(UUID(po["id"]), MatchRequest(invoice_id=bill.id), uk)
    assert res["matched"] is False
    assert any(d["type"] == "short_receipt" for d in res["discrepancies"])


def test_over_receipt_rejected(uk):
    sup = _supplier(uk)
    po = create_po(POCreate(
        entity_id=sup.id, order_date=date(2025, 6, 1),
        lines=[POLineInput(description="Widget", ordered_qty=10, unit_price=100)],
    ), uk)
    po_line_id = UUID(po["lines"][0]["id"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        record_receipt(UUID(po["id"]), ReceiptCreate(
            receipt_date=date(2025, 6, 5),
            lines=[ReceiptLineInput(po_line_id=po_line_id, quantity=12)],
        ), uk)
    assert ei.value.status_code == 422


def test_match_rejects_non_purchase_invoice(uk):
    sup = _supplier(uk)
    po = create_po(POCreate(
        entity_id=sup.id, order_date=date(2025, 6, 1),
        lines=[POLineInput(description="Widget", ordered_qty=1, unit_price=100)],
    ), uk)
    sales = Invoice(number="INV-9", kind="sales", status="issued",
                    issue_date=date(2025, 6, 1), due_date=date(2025, 7, 1), currency="GBP")
    uk.add(sales)
    uk.commit()
    uk.refresh(sales)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        match_po(UUID(po["id"]), MatchRequest(invoice_id=sales.id), uk)
    assert ei.value.status_code == 422
