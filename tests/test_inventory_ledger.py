"""Stock in the books (docs/PLAN_INVENTORY_GL.md, phase G1; scenarios T1–T10).

Stock was valued from its movements but never reached the ledger: no stock on
the balance sheet, no cost of what was sold. With the switch on, the stock
account follows the valuation month by month, under either costing method,
through back-dated movements, and without touching a closed month."""
from __future__ import annotations

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
