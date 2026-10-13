"""Stock in the books (docs/PLAN_INVENTORY_GL.md, phases G1 and G2; scenarios T1–T14).

Stock was valued from its movements but never reached the ledger: no stock on
the balance sheet, no cost of what was sold. With the switch on, the stock
account follows the valuation month by month, under either costing method,
through back-dated movements, and without touching a closed month."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from tests.test_statement_export import _company

TODAY = date.today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - timedelta(days=1)).replace(day=1)
URL = "/manager-reports/inventory"


def _books(kind):
    def fixture(client, db):
        from tests.test_admin_audit import _purge_company
        api, cid = _company(client, db, *kind)
        yield api, cid
        client.cookies.clear()
        _purge_company(db, cid)
    return pytest.fixture(name="uk" if kind[0] == "uk" else "ir")(fixture)


uk = _books(("uk", "GBP"))
ir = _books(("ir", "IRR"))


def _item(api, name):
    r = api.post(f"{URL}/items", json={"name": name, "unit": "unit"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _move(api, item, typ, qty, cost=0, on=None, expect=201):
    r = api.post(f"{URL}/movements", json={"item_id": item, "movement_type": typ, "quantity": qty,
                                           "unit_cost": cost, "movement_date": str(on or TODAY)})
    assert r.status_code == expect, r.text
    return r


def _status(api):
    r = api.get(f"{URL}/ledger")
    assert r.status_code == 200, r.text
    return r.json()


def _on(api, start=THIS_MONTH, expect=200):
    r = api.put(f"{URL}/ledger", json={"enabled": True, "from": str(start)})
    assert r.status_code == expect, r.text
    return r.json()


def _stk(db, cid, ref=None):
    """The stock journals' lines: (reference, account code, debit, credit) —
    every one, or a single journal's (debit first)."""
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    with use_company(cid):
        rows = db.execute(select(Transaction.reference, Account.code, TransactionLine.debit, TransactionLine.credit)
                          .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
                          .join(Account, Account.id == TransactionLine.account_id)
                          .where(Transaction.reference == ref if ref else Transaction.reference.like("STK-%"))
                          .order_by(Transaction.created_at, Transaction.id, TransactionLine.debit.desc())).all()
    return [tuple(r) for r in rows]


def _agrees(api):
    s = _status(api)
    assert s["books_value"] == s["valuation"] and s["difference"] == 0, s
    return s["books_value"]


def test_t1_switching_on_brings_the_stock_onto_the_books(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _move(api, widget, "IN", 20, 75, on=LAST_MONTH)
    assert _stk(db, cid) == []                                       # off: nothing posts (T2)
    s = _on(api)
    assert s["enabled"] and s["from"] == str(THIS_MONTH) and s["books_value"] == 1500 == s["valuation"]
    assert _stk(db, cid) == [("STK-OPEN", "1000", 1500, 0), ("STK-OPEN", "5900", 0, 1500)]
    _on(api)                                                         # on again: nothing more
    assert len(_stk(db, cid)) == 2
    assert _on(api, THIS_MONTH - timedelta(days=3), expect=422)      # the start can't move
    bs = {r["key"]: r["amount_current"] for r in api.get("/manager-reports/financial/uk/balance-sheet").json()["rows"]}
    assert bs["ca_stocks"] == 1500


def test_t2_off_by_default_nothing_posts(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _move(api, widget, "IN", 4, 10)
    _move(api, widget, "OUT", 1)
    s = _status(api)
    assert not s["enabled"] and s["from"] is None and s["difference"] is None and s["valuation"] == 30
    assert _stk(db, cid) == []
    assert api.post(f"{URL}/ledger/sync").json()["posted"] == []


def test_t3_t4_t5_movements_by_hand_and_production_post_as_adjustments(uk, db):
    api, cid = uk
    widget, bolt, gadget = _item(api, "Widget"), _item(api, "Bolt"), _item(api, "Gadget")
    _move(api, widget, "IN", 20, 75, on=LAST_MONTH)
    _on(api)
    _move(api, widget, "OUT", 2)                                       # T3: two damaged
    month = f"STK-{THIS_MONTH:%Y-%m}"
    assert _stk(db, cid, month) == [(month, "5900", 150, 0), (month, "1000", 0, 150)]
    assert _agrees(api) == 1350
    _move(api, bolt, "IN", 5, "12.50")                                 # T4: found, with pence
    assert _agrees(api) == Decimal("1412.5")
    assert api.put(f"{URL}/items/{gadget}/bom", json={"lines": [
        {"component_id": widget, "quantity": 1}, {"component_id": bolt, "quantity": 2}]}).status_code == 200
    before = len(_stk(db, cid))
    r = api.post(f"{URL}/production", json={"product_id": gadget, "quantity": 2, "on": str(TODAY)})
    assert r.status_code == 201, r.text                                # T5: value moves within stock
    assert len(_stk(db, cid)) == before and _agrees(api) == Decimal("1412.5")


def test_t6_a_backdated_purchase_is_caught_up_and_t9_twice_posts_nothing(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _move(api, widget, "IN", 10, 100, on=LAST_MONTH)
    _on(api)
    _move(api, widget, "OUT", 5)                                       # at 100
    assert _agrees(api) == 500
    _move(api, widget, "IN", 10, 160, on=THIS_MONTH)                   # earlier than the OUT
    # average now (1000 + 1600) / 20 = 130 before the OUT: the OUT costs 650, not 500
    assert _agrees(api) == 2600 - 650
    n = len(_stk(db, cid))
    assert api.post(f"{URL}/ledger/sync").json()["posted"] == []       # T9
    assert len(_stk(db, cid)) == n


def test_t7_changing_the_method_and_t8_a_closed_month(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api, LAST_MONTH)
    _move(api, widget, "IN", 10, 100, on=LAST_MONTH)
    _move(api, widget, "IN", 10, 200, on=LAST_MONTH + timedelta(days=1))
    _move(api, widget, "OUT", 15, on=LAST_MONTH + timedelta(days=2))   # weighted average: 2,250
    assert _agrees(api) == 750
    from tests.test_closed_period_edits import _lock
    _lock(api, str(THIS_MONTH - timedelta(days=1)))                    # T8: last month closed
    try:
        _move(api, widget, "IN", 1, 100, on=LAST_MONTH + timedelta(days=3), expect=422)
        r = api.put(f"{URL}/settings", json={"method": "fifo"})        # T7
        assert r.status_code == 200, r.text
        assert _agrees(api) == 1000                                    # FIFO: 5 left at 200
        month = f"STK-{THIS_MONTH:%Y-%m}"
        assert _stk(db, cid, month) == [(month, "1000", 250, 0), (month, "5900", 0, 250)]   # the catch-up
        with use_company(cid):
            from app.models.inventory import InventoryPosting
            kinds = [p.kind for p in db.execute(select(InventoryPosting).order_by(InventoryPosting.created_at)).scalars()]
        assert kinds[-1] == "catchup"
        assert api.post(f"{URL}/ledger/sync").json()["posted"] == []   # caught up once
    finally:
        _lock(api, None)


def test_switching_off_stops_posting_and_on_again_resumes(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _move(api, widget, "IN", 2, 50, on=LAST_MONTH)
    _on(api)
    assert api.put(f"{URL}/ledger", json={"enabled": False}).json()["enabled"] is False
    _move(api, widget, "OUT", 1)
    n = len(_stk(db, cid))
    assert _status(api)["books_value"] == 100 and _status(api)["valuation"] == 50
    s = api.put(f"{URL}/ledger", json={"enabled": True}).json()       # back on, from the same date
    assert s["from"] == str(THIS_MONTH) and s["books_value"] == 50 == s["valuation"]
    assert len(_stk(db, cid)) == n + 2


def test_a_start_date_must_be_given_open_and_not_ahead(uk):
    api, _ = uk
    assert api.put(f"{URL}/ledger", json={"enabled": True}).status_code == 422
    assert _on(api, TODAY + timedelta(days=2), expect=422)


def test_t10_an_iranian_company_gets_its_accounts_and_statement_lines(ir, db):
    api, cid = ir
    rice = _item(api, "برنج")
    _move(api, rice, "IN", 10, 2_000_000, on=LAST_MONTH)
    _on(api)
    _move(api, rice, "OUT", 3)
    from app.models.account import Account
    with use_company(cid):
        accounts = {a.code: a for a in db.execute(select(Account)).scalars()}
    assert accounts["1145"].name == "موجودی مواد و کالا" and accounts["1145"].parent_id == accounts["11"].id
    assert accounts["51"].level.value.upper() == "GROUP"
    assert accounts["5190"].parent_id == accounts["51"].id
    assert _agrees(api) == 14_000_000
    bs = {r["key"]: r["amount_current"] for r in api.get("/manager-reports/financial/iran/balance-sheet").json()["rows"]}
    assert bs["ca_inventory"] == 14_000_000
    from app.models.transaction import Transaction
    with use_company(cid):
        words = db.execute(select(Transaction.description).where(Transaction.reference.like("STK-%"))).scalars().all()
    assert "ثبت موجودی کالای ابتدای دوره در دفاتر" in words
    assert any(w.startswith("تعدیل موجودی کالا — ") for w in words)          # the month in Jalali words


# --- invoices move stock (phase G2; scenarios T11–T13) ----------------------------------------------------------

def _invoice(api, kind, number, lines, *, on=None, entity=None):
    body = {"number": number, "kind": kind, "status": "issued", "issue_date": str(on or TODAY),
            "due_date": str((on or TODAY) + timedelta(days=30)), "amount": 0, "currency": "GBP",
            "items": [{"product_name": n, "quantity": q, "unit_price": p, "tax_rate": 0,
                       **({"inventory_item_id": i} if i else {})} for n, q, p, i in lines]}
    if entity:
        body["entity_id"] = entity
    r = api.post("/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _lines(db, cid, ref):
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    with use_company(cid):
        rows = db.execute(select(Account.code, TransactionLine.debit, TransactionLine.credit)
                          .join(TransactionLine, TransactionLine.account_id == Account.id)
                          .join(Transaction, Transaction.id == TransactionLine.transaction_id)
                          .where(Transaction.reference == ref)).all()
    return sorted(tuple(r) for r in rows)


def _movements(db, cid, invoice_id):
    from app.models.inventory import InventoryMovement
    with use_company(cid):
        return [(m.movement_type.value, float(m.quantity), m.ledger_value)
                for m in db.execute(select(InventoryMovement).where(InventoryMovement.invoice_id == uuid.UUID(invoice_id))
                                    .order_by(InventoryMovement.created_at)).scalars()]


def test_t11_buying_stock_on_an_invoice(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    supplier = api.post("/entities", json={"type": "supplier", "name": "Northwind"}).json()["id"]
    bill = _invoice(api, "purchase", "NW-1", [("Widget", 10, 100, widget), ("Delivery", 1, 20, None)], entity=supplier)
    assert _lines(db, cid, "NW-1") == [("1000", 1000, 0), ("2100", 0, 1020), ("5000", 20, 0)]
    assert _movements(db, cid, bill["id"]) == [("IN", 10.0, 1000)]
    assert _agrees(api) == 1000


def test_t12_t13_selling_stock_then_voiding_the_sale(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    _invoice(api, "purchase", "B-1", [("Widget", 10, 100, widget)])
    _invoice(api, "purchase", "B-2", [("Widget", 10, 200, widget)])
    sale = _invoice(api, "sales", "S-1", [("Widget", 15, 300, widget)])
    assert _movements(db, cid, sale["id"]) == [("OUT", 15.0, None)]
    month = f"STK-{THIS_MONTH:%Y-%m}"
    assert ("5010", 2250, 0) in _lines(db, cid, month)                  # weighted average: 15 × 150
    assert _agrees(api) == 750
    pl = {r["key"]: r["amount_current"] for r in api.get("/manager-reports/financial/uk/profit-and-loss").json()["rows"]}
    assert pl["cost_of_sales"] == -2250                                 # shown as a deduction
    assert api.put(f"{URL}/settings", json={"method": "fifo"}).status_code == 200
    assert _agrees(api) == 1000                                         # FIFO: 10 × 100 + 5 × 100
    r = api.post(f"/invoices/{sale['id']}/void")
    assert r.status_code == 200, r.text                                 # T13
    assert _movements(db, cid, sale["id"]) == [("OUT", 15.0, None), ("IN", 15.0, None)]
    assert _agrees(api) == 3000                                         # back at what it cost
    pl = {r["key"]: r["amount_current"] for r in api.get("/manager-reports/financial/uk/profit-and-loss").json()["rows"]}
    assert pl["cost_of_sales"] == 0


def test_voiding_a_purchase_takes_its_stock_out_again(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    bill = _invoice(api, "purchase", "B-9", [("Widget", 3, "12.50", widget)])
    assert _agrees(api) == Decimal("37.5")
    assert api.post(f"/invoices/{bill['id']}/void").status_code == 200
    assert _agrees(api) == 0


def test_invoices_before_the_start_or_while_off_move_nothing(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    early = _invoice(api, "purchase", "OLD-1", [("Widget", 5, 10, widget)], on=LAST_MONTH)
    assert _movements(db, cid, early["id"]) == []                       # off
    assert _lines(db, cid, "OLD-1") == [("2100", 0, 50), ("5000", 50, 0)]
    _on(api)
    late = _invoice(api, "purchase", "OLD-2", [("Widget", 5, 10, widget)], on=THIS_MONTH - timedelta(days=1))
    assert _movements(db, cid, late["id"]) == []                        # dated before the start


def test_a_foreign_purchase_without_a_rate_is_refused_in_words(uk, db):
    api, cid = uk
    widget = _item(api, "Widget")
    _on(api)
    r = api.post("/invoices", json={"number": "US-1", "kind": "purchase", "status": "issued", "issue_date": str(TODAY),
                                    "due_date": str(TODAY + timedelta(days=30)), "amount": 0, "currency": "USD",
                                    "items": [{"product_name": "Widget", "quantity": 2, "unit_price": 40, "tax_rate": 0,
                                               "inventory_item_id": widget}]})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (f"Add the exchange rate for USD on {TODAY.isoformat()} first: "
                                  "stock is kept in the base currency.")
    assert _status(api)["valuation"] == 0 and _agrees(api) == 0


def test_t14_a_month_whose_cost_of_sales_is_a_credit(uk, db):
    """The stock brought on (credited through cost of sales, the UK closing-stock
    entry) outweighs the month's costs: the P&L and its exports show a credit,
    not «(750)», a cost, beside a gross profit that counted it as a credit."""
    from tests.test_statement_export import _pdf_text, _rows, _xlsx
    api, cid = uk
    widget = _item(api, "Widget")
    _move(api, widget, "IN", 20, 75, on=LAST_MONTH)
    _on(api)                                                            # STK-OPEN credits 5900 with 1,500
    _invoice(api, "purchase", "D-1", [("Delivery", 1, 300, None)])
    _invoice(api, "sales", "S-14", [("Widget", 6, 120, widget)])        # costs 6 × 75
    period = {"from_date": str(THIS_MONTH), "to_date": str(TODAY)}
    pl = {r["key"]: r["amount_current"] for r in
          api.get("/manager-reports/financial/uk/profit-and-loss", params=period).json()["rows"]}
    assert pl["cost_of_sales"] == 750                                   # 300 + 450 − 1,500
    assert pl["gross_profit"] == 720 + 750
    export = {**period, "statements": "income_statement"}
    sheet = _rows(_xlsx(api.get("/manager-reports/financial/export", params={**export, "format": "xlsx"}))[
        "Profit and loss account"])
    assert sheet["Cost of sales"][0] == 750 and sheet["Gross profit/(loss)"][0] == 1470
    _pages, text = _pdf_text(api.get("/manager-reports/financial/export", params={**export, "format": "pdf"}))
    assert "750" in text and "(750" not in text
