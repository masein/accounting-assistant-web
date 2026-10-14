"""Stock in the books, phase G3 (docs/PLAN_INVENTORY_GL.md; scenarios T15–T23).

Goods coming back on a credit note, a stock count, the assistant reading the
stock the books carry, and the month-end close. With stock posting on, the
stock account must still equal the valuation after each of them."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.db.tenant import use_company
from tests.test_inventory_ledger import (  # noqa: F401  (uk is a fixture)
    LAST_MONTH, THIS_MONTH, TODAY, URL, _agrees, _invoice, _item, _move, _movements, _on, _stk, uk,
)


def _bill(api, number, lines, *, on=None):
    """A bill with VAT: lines are (name, quantity, price, item, rate)."""
    body = {"number": number, "kind": "purchase", "status": "issued", "issue_date": str(on or TODAY),
            "due_date": str((on or TODAY) + timedelta(days=30)), "amount": 0, "currency": "GBP",
            "items": [{"product_name": n, "quantity": q, "unit_price": p, "tax_rate": rate,
                       **({"inventory_item_id": i} if i else {})} for n, q, p, i, rate in lines]}
    r = api.post("/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _credit(api, inv, amount, returns=(), expect=201):
    r = api.post(f"/invoices/{inv['id']}/credit-notes",
                 json={"amount": amount, "returns": [{"item_id": i, "quantity": q} for i, q in returns]})
    assert r.status_code == expect, r.text
    return r.json()


def _entry(db, cid, transaction_id):
    """A journal's lines: sorted (account code, debit, credit)."""
    from app.models.account import Account
    from app.models.transaction import TransactionLine
    with use_company(cid):
        rows = db.execute(select(Account.code, TransactionLine.debit, TransactionLine.credit)
                          .join(TransactionLine, TransactionLine.account_id == Account.id)
                          .where(TransactionLine.transaction_id == uuid.UUID(transaction_id))).all()
    return sorted(tuple(r) for r in rows)


def _cost_of_sales(api):
    rows = api.get("/manager-reports/financial/uk/profit-and-loss").json()["rows"]
    return {r["key"]: r["amount_current"] for r in rows}["cost_of_sales"]


def _left(api, inv):
    r = api.get(f"/invoices/{inv['id']}/stock-returns")
    assert r.status_code == 200, r.text
    return r.json()


def _sold_fifteen(api):
    """T12's books: 10 Widgets at £100 and 10 at £200 bought, 15 sold at £300."""
    widget = _item(api, "Widget")
    _on(api)
    _invoice(api, "purchase", "B-1", [("Widget", 10, 100, widget)])
    _invoice(api, "purchase", "B-2", [("Widget", 10, 200, widget)])
    sale = _invoice(api, "sales", "S-1", [("Widget", 15, 300, widget)])
    return widget, sale


# --- goods back on a credit note ----------------------------------------------------------------------------------

def test_t15_a_customer_returns_goods(uk, db):
    api, cid = uk
    widget, sale = _sold_fifteen(api)
    before = _left(api, sale)
    assert before["enabled"] is True
    assert before["lines"] == [{"item_id": widget, "name": "Widget", "unit": "unit", "moved": 15, "back": 0,
                                "left": 15, "unit_price": 300, "unit_gross": 300}]
    assert _cost_of_sales(api) == -2250
    note = _credit(api, sale, 1500, [(widget, 5)])
    # the revenue side as ever: sales returns against what the customer owes
    codes = [c for c, _d, _c in _entry(db, cid, note["transaction_id"])]
    assert len(codes) == 2 and "1100" in codes
    assert _movements(db, cid, sale["id"]) == [("OUT", 15.0, None), ("IN", 5.0, None)]
    assert _cost_of_sales(api) == -1500                                 # 5 back at the £150 they cost
    assert _agrees(api) == 1500
    after = _left(api, sale)["lines"][0]
    assert (after["back"], after["left"]) == (5, 10)


def test_t16_goods_go_back_to_the_supplier(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    bill = _bill(api, "NW-2", [("Widget", 10, 100, widget, 20)])
    assert _agrees(api) == 1000
    assert _left(api, bill)["lines"][0]["unit_gross"] == 120
    note = _credit(api, bill, 480, [(widget, 4)])
    lines = _entry(db, cid, note["transaction_id"])
    assert ("1000", 0, 400) in lines                                    # Stocks, not purchases
    assert ("2100", 480, 0) in lines
    assert [ln for ln in lines if ln[0] == "5000"] == []
    assert sum(d for _c, d, _cr in lines) == sum(c for _c, _d, c in lines) == 480
    assert _movements(db, cid, bill["id"]) == [("IN", 10.0, 1000), ("OUT", 4.0, -400)]
    assert _agrees(api) == 600


def test_t17_the_supplier_keeps_a_restocking_fee(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    bill = _bill(api, "NW-3", [("Widget", 10, 100, widget, 20)])
    note = _credit(api, bill, 432, [(widget, 4)])
    lines = _entry(db, cid, note["transaction_id"])
    assert ("1000", 0, 400) in lines and ("2100", 432, 0) in lines and ("5000", 40, 0) in lines
    assert sum(d for _c, d, _cr in lines) == sum(c for _c, _d, c in lines) == 472     # with VAT £72
    assert _agrees(api) == 600


def test_t18_what_cant_come_back_is_refused_and_nothing_posts(uk, db):
    from app.models.credit_note import CreditNote
    from app.models.inventory import InventoryMovement
    api, cid = uk
    widget, sale = _sold_fifteen(api)
    gadget = _item(api, "Gadget")
    _credit(api, sale, 1500, [(widget, 5)])

    def counts():
        with use_company(cid):
            return (db.execute(select(func.count()).select_from(CreditNote)).scalar(),
                    db.execute(select(func.count()).select_from(InventoryMovement)).scalar())

    before = counts()
    r = _credit(api, sale, 3000, [(widget, 11)], expect=422)
    assert r["detail"] == "Only 10 Widget can come back on S-1."
    r = _credit(api, sale, 300, [(gadget, 1)], expect=422)
    assert r["detail"] == "Gadget wasn't on S-1, so it can't come back on its credit note."
    old = _invoice(api, "sales", "S-0", [("Widget", 1, 300, widget)], on=LAST_MONTH)   # before the start
    r = _credit(api, old, 300, [(widget, 1)], expect=422)
    assert r["detail"] == "S-0 moved no stock: record goods coming back on the Inventory page."
    assert api.put(f"{URL}/ledger", json={"enabled": False}).status_code == 200
    r = _credit(api, sale, 300, [(widget, 1)], expect=422)
    assert r["detail"] == "Turn on stock in the books (Inventory) to take goods back on a credit note."
    assert counts() == before                                           # S-0 moved nothing either: before the start
    # a credit note with no goods back still works, as it always did
    _credit(api, sale, 100)


def test_t19_voiding_after_a_return_reverses_each_movement(uk, db):
    api, cid = uk
    widget, sale = _sold_fifteen(api)
    _credit(api, sale, 1500, [(widget, 5)])
    r = api.post(f"/invoices/{sale['id']}/void")
    assert r.status_code == 200, r.text
    assert _movements(db, cid, sale["id"]) == [("OUT", 15.0, None), ("IN", 5.0, None),
                                               ("IN", 15.0, None), ("OUT", 5.0, None)]
    assert _cost_of_sales(api) == 0
    assert _agrees(api) == 3000


def test_voiding_a_bill_after_goods_went_back(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    bill = _bill(api, "NW-4", [("Widget", 10, 100, widget, 20)])
    _credit(api, bill, 480, [(widget, 4)])
    assert api.post(f"/invoices/{bill['id']}/void").status_code == 200
    assert _movements(db, cid, bill["id"]) == [("IN", 10.0, 1000), ("OUT", 4.0, -400),
                                               ("IN", 4.0, 400), ("OUT", 10.0, -1000)]
    assert _agrees(api) == 0


# --- a stock count -----------------------------------------------------------------------------------------------

def test_t20_a_stock_count_posts_what_differs(uk, db):
    from app.models.inventory import InventoryMovement
    api, cid = uk
    widget, gadget, sprocket = _item(api, "Widget"), _item(api, "Gadget"), _item(api, "Sprocket")
    _on(api)
    _move(api, widget, "IN", 10, 100)
    _move(api, gadget, "IN", 11, 50)
    _move(api, sprocket, "IN", 4, 10)
    assert _agrees(api) == 1590
    sheet = api.get(f"{URL}/count-sheet").json()
    assert {r["name"]: r["on_hand"] for r in sheet["rows"]} == {"Gadget": 11, "Sprocket": 4, "Widget": 10}
    body = {"date": str(TODAY), "lines": [{"item_id": widget, "counted": 8}, {"item_id": gadget, "counted": 12}]}
    r = api.post(f"{URL}/counts", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert [(a["name"], a["difference"], a["value"]) for a in out["adjusted"]] == [("Widget", -2, -200),
                                                                                  ("Gadget", 1, 50)]
    with use_company(cid):
        moved = sorted((m.movement_type.value, float(m.quantity)) for m in db.execute(
            select(InventoryMovement).where(InventoryMovement.reference == f"COUNT-{TODAY}")).scalars())
    assert moved == [("IN", 1.0), ("OUT", 2.0)]
    assert _agrees(api) == 1440
    month = f"STK-{THIS_MONTH:%Y-%m}"
    lines = _stk(db, cid, month)
    assert (month, "5900", 150, 0) in lines and (month, "1000", 0, 150) in lines              # one adjustment
    again = api.post(f"{URL}/counts", json=body).json()
    assert again["adjusted"] == [] and again["posted"] == []


def test_t21_a_count_in_a_closed_month_is_refused(uk, db):
    from tests.test_closed_period_edits import _lock
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api, LAST_MONTH)
    _move(api, widget, "IN", 10, 100, on=LAST_MONTH)
    _lock(api, str(THIS_MONTH - timedelta(days=1)))
    try:
        r = api.post(f"{URL}/counts", json={"date": str(LAST_MONTH + timedelta(days=3)),
                                            "lines": [{"item_id": widget, "counted": 9}]})
        assert r.status_code == 422, r.text
        assert _agrees(api) == 1000
    finally:
        _lock(api, None)


# --- the assistant and the close --------------------------------------------------------------------------------

def test_t22_the_assistant_reads_what_the_books_carry(uk, db):
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.inventory_tools import GetInventory, GetInventoryInput
    api, cid = uk
    widget = _item(api, "Widget")
    _move(api, widget, "IN", 3, 100)
    tool = GetInventory()
    with use_company(cid):
        off = asyncio.run(tool.run(ToolContext(db=db, user_id="u", username="owner"), GetInventoryInput()))
    assert off["books"]["posting"] is False
    _on(api)
    with use_company(cid):
        on = asyncio.run(tool.run(ToolContext(db=db, user_id="u", username="owner"), GetInventoryInput()))
    assert on["books"] == {"posting": True, "from": str(THIS_MONTH), "books_value": 300, "valuation": 300,
                           "difference": 0, "agrees": True}


def test_t23_the_close_checklist_and_locking_post_the_stock_first(uk, db):
    from app.models.inventory import InventoryMovement, InventoryMovementType
    from tests.test_close_pack import _checklist, _detail, _states
    from tests.test_closed_period_edits import _lock
    api, cid = uk
    widget = _item(api, "Widget")
    last = f"{LAST_MONTH:%Y-%m}"
    assert "stock" not in _states(_checklist(api, last))                # not posting: no line
    _on(api, LAST_MONTH)
    _move(api, widget, "IN", 10, 100, on=LAST_MONTH)
    data = _checklist(api, last)
    assert _states(data)["stock"] == "ok" and "1,000" in _detail(data, "stock")
    # a movement the sync hasn't seen yet (as a failed sync would leave it)
    with use_company(cid):
        db.add(InventoryMovement(item_id=uuid.UUID(widget), movement_date=LAST_MONTH + timedelta(days=1),
                                 movement_type=InventoryMovementType.IN, quantity=2, unit_cost=100))
        db.commit()
    data = _checklist(api, last)
    assert _states(data)["stock"] == "warn"
    assert "1,000" in _detail(data, "stock") and "1,200" in _detail(data, "stock")
    _lock(api, str(THIS_MONTH - timedelta(days=1)))                    # locking posts the stock first
    try:
        assert _states(_checklist(api, last))["stock"] == "ok"
        assert _agrees(api) == 1200
        assert api.post(f"{URL}/ledger/sync").json()["posted"] == []    # nothing left for an open month
    finally:
        _lock(api, None)
