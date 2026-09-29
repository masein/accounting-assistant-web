"""Purchase orders: their lifecycle, bills from what arrived, and supplier prices (roadmap §4.8)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.transaction import Transaction
from tests.test_time_billing_and_fees_http import _count, _ent, co  # noqa: F401 — fixture


def _po(api, supplier, lines, *, status="issued", order_date="2026-09-01", currency=None):
    body = {"entity_id": supplier["id"] if supplier else None, "order_date": order_date, "status": status,
            "lines": [{"description": d, "ordered_qty": q, "unit_price": p} for d, q, p in lines]}
    if currency:
        body["currency"] = currency
    r = api.post("/purchase-orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _receive(api, po, qty_by_desc, when="2026-09-05"):
    lines = [{"po_line_id": ln["id"], "quantity": qty_by_desc[ln["description"]]}
             for ln in po["lines"] if qty_by_desc.get(ln["description"])]
    r = api.post(f"/purchase-orders/{po['id']}/receipts", json={"receipt_date": when, "lines": lines})
    assert r.status_code == 201, r.text
    return r.json()


def _status(api, po, status):
    return api.patch(f"/purchase-orders/{po['id']}", json={"status": status})


# --- the lifecycle -----------------------------------------------------------------------------------------------

def test_an_order_moves_along_its_lifecycle_and_not_backwards(co):
    api, _cid = co
    sup = _ent(api, "supplier", "Pars Paper")
    draft = _po(api, sup, [("A4 paper", 10, 50_000)], status="draft")
    assert _status(api, draft, "received").status_code == 422          # receipts decide that
    assert _status(api, draft, "closed").status_code == 422            # a draft is issued or cancelled
    assert _status(api, draft, "issued").json()["status"] == "issued"
    po = api.get(f"/purchase-orders/{draft['id']}").json()
    _receive(api, po, {"A4 paper": 4})
    assert api.get(f"/purchase-orders/{po['id']}").json()["status"] == "partially_received"
    r = _status(api, po, "cancelled")
    assert r.status_code == 409 and "close it" in r.json()["detail"]   # goods arrived: close, don't cancel
    assert _status(api, po, "closed").json()["status"] == "closed"
    assert _status(api, po, "issued").status_code == 409               # closed is final
    r = api.post(f"/purchase-orders/{po['id']}/receipts",
                 json={"receipt_date": "2026-09-06", "lines": [{"po_line_id": po["lines"][0]["id"], "quantity": 1}]})
    assert r.status_code == 409
    other = _po(api, sup, [("Toner", 2, 900_000)])
    assert _status(api, other, "cancelled").json()["status"] == "cancelled"
    assert _status(api, other, "issued").status_code == 409            # cancelled is final
    assert _status(api, other, "whatever").status_code == 422


def test_only_a_draft_is_deleted(co, db):
    from app.models.purchase_order import PurchaseOrder
    api, cid = co
    sup = _ent(api, "supplier")
    draft = _po(api, sup, [("Pens", 100, 5_000)], status="draft")
    issued = _po(api, sup, [("Pens", 100, 5_000)])
    r = api.delete(f"/purchase-orders/{issued['id']}")
    assert r.status_code == 409 and "cancel or close" in r.json()["detail"]
    assert api.delete(f"/purchase-orders/{draft['id']}").status_code == 204
    assert api.get(f"/purchase-orders/{draft['id']}").status_code == 404
    assert api.delete(f"/purchase-orders/{uuid.uuid4()}").status_code == 404
    assert _count(db, cid, PurchaseOrder) == 1


# --- bills from what arrived -------------------------------------------------------------------------------------

def test_what_arrived_is_billed_once_and_a_void_gives_it_back(co, db):
    api, cid = co
    sup = _ent(api, "supplier", "Tehran Steel")
    po = _po(api, sup, [("Steel sheet", 10, 1_200_000), ("Bolts", 500, 2_000)])
    r = api.post(f"/purchase-orders/{po['id']}/bill", json={})
    assert r.status_code == 422 and "arrived" in r.json()["detail"]    # nothing has arrived yet
    po = _receive(api, po, {"Steel sheet": 4, "Bolts": 500})
    txns = _count(db, cid, Transaction)
    r = api.post(f"/purchase-orders/{po['id']}/bill", json={"number": "TS-7781", "issue_date": "2026-09-06"})
    assert r.status_code == 201, r.text
    bill = r.json()
    assert (bill["invoice_number"], bill["amount"]) == ("TS-7781", 4 * 1_200_000 + 500 * 2_000)
    assert _count(db, cid, Transaction) == txns + 1                    # the payable is recognised
    order = bill["order"]
    lines = {ln["description"]: ln for ln in order["lines"]}
    assert (lines["Steel sheet"]["billed_qty"], lines["Steel sheet"]["billable_qty"]) == (4, 0)
    assert order["billing_status"] == "partially_billed" and [b["number"] for b in order["bills"]] == ["TS-7781"]
    with use_company(cid):
        inv = db.execute(select(Invoice).where(Invoice.number == "TS-7781")).scalars().one()
        assert (inv.kind, inv.status, str(inv.entity_id), str(inv.purchase_order_id)) == ("purchase", "issued", sup["id"], po["id"])
        items = db.execute(select(InvoiceItem).where(InvoiceItem.invoice_id == inv.id)).scalars().all()
        assert {str(i.po_line_id) for i in items} == {ln["id"] for ln in po["lines"]}
    # nothing more to bill until more arrives; then only the new goods
    assert api.post(f"/purchase-orders/{po['id']}/bill", json={}).status_code == 422
    _receive(api, api.get(f"/purchase-orders/{po['id']}").json(), {"Steel sheet": 6}, when="2026-09-10")
    second = api.post(f"/purchase-orders/{po['id']}/bill", json={}).json()
    assert second["amount"] == 6 * 1_200_000 and second["invoice_number"].startswith("BILL-")
    assert second["order"]["billing_status"] == "billed"
    # voiding the first bill puts its quantities back to be billed again
    assert api.post(f"/invoices/{bill['invoice_id']}/void").status_code == 200
    again = {ln["description"]: ln for ln in api.get(f"/purchase-orders/{po['id']}").json()["lines"]}
    assert (again["Steel sheet"]["billed_qty"], again["Steel sheet"]["billable_qty"]) == (6, 4)
    assert (again["Bolts"]["billed_qty"], again["Bolts"]["billable_qty"]) == (0, 500)


def test_chosen_quantities_are_billed_and_never_more_than_arrived(co):
    api, _cid = co
    sup = _ent(api, "supplier")
    po = _receive(api, _po(api, sup, [("Cement bag", 100, 150_000)]), {"Cement bag": 60})
    line = po["lines"][0]["id"]
    r = api.post(f"/purchase-orders/{po['id']}/bill", json={"lines": [{"po_line_id": line, "quantity": 61}]})
    assert r.status_code == 422 and "more than has arrived" in r.json()["detail"]
    r = api.post(f"/purchase-orders/{po['id']}/bill", json={"lines": [{"po_line_id": str(uuid.uuid4()), "quantity": 1}]})
    assert r.status_code == 422
    r = api.post(f"/purchase-orders/{po['id']}/bill", json={"lines": [{"po_line_id": line, "quantity": 25}], "tax_rate": 10})
    assert r.status_code == 201 and r.json()["amount"] == 25 * 150_000 + 25 * 150_000 // 10    # with 10 % VAT
    assert api.get(f"/purchase-orders/{po['id']}").json()["lines"][0]["billable_qty"] == 35


def test_a_short_closed_order_bills_what_arrived(co):
    api, _cid = co
    sup = _ent(api, "supplier")
    po = _receive(api, _po(api, sup, [("Chairs", 20, 3_000_000)]), {"Chairs": 12})
    assert _status(api, po, "closed").status_code == 200
    r = api.post(f"/purchase-orders/{po['id']}/bill", json={})
    assert r.status_code == 201 and r.json()["amount"] == 12 * 3_000_000
    assert r.json()["order"]["billing_status"] == "billed"                # all that will ever arrive


def test_orders_that_cannot_be_billed(co):
    api, _cid = co
    sup = _ent(api, "supplier")
    draft = _po(api, sup, [("X", 1, 1)], status="draft")
    assert api.post(f"/purchase-orders/{draft['id']}/bill", json={}).status_code == 409
    nobody = _receive(api, _po(api, None, [("X", 1, 1_000)]), {"X": 1})
    r = api.post(f"/purchase-orders/{nobody['id']}/bill", json={})
    assert r.status_code == 422 and "supplier" in r.json()["detail"]
    assert api.post(f"/purchase-orders/{uuid.uuid4()}/bill", json={}).status_code == 404


# --- supplier prices ---------------------------------------------------------------------------------------------

def test_price_history_across_orders_and_bills(co):
    api, _cid = co
    a, b = _ent(api, "supplier", "Alpha Paper"), _ent(api, "supplier", "Beta Paper")
    _po(api, a, [("A4 paper 80g", 10, 52_000)], order_date="2026-06-01")
    _po(api, b, [("A4 paper 80g", 20, 49_000)], order_date="2026-07-01")
    _po(api, a, [("A4 paper 80g", 5, 55_000)], order_date="2026-08-01")
    cancelled = _po(api, b, [("A4 paper 80g", 5, 10_000)], order_date="2026-08-15")
    _status(api, cancelled, "cancelled")                                 # not a price anyone paid
    r = api.post("/invoices", json={"number": "BP-1", "kind": "purchase", "issue_date": "2026-09-01",
                                    "due_date": "2026-09-30", "amount": 0, "entity_id": b["id"],
                                    "items": [{"product_name": "A4 paper 80g", "quantity": 10, "unit_price": 51_000}]})
    assert r.status_code == 201, r.text
    _po(api, a, [("Toner", 1, 900_000)])
    h = api.get("/purchase-orders/price-history", params={"q": "a4 paper"}).json()
    assert [(x["date"], x["source"], x["supplier"], x["unit_price"]) for x in h["rows"]] == [
        ("2026-09-01", "bill", "Beta Paper", 51_000), ("2026-08-01", "order", "Alpha Paper", 55_000),
        ("2026-07-01", "order", "Beta Paper", 49_000), ("2026-06-01", "order", "Alpha Paper", 52_000)]
    s = h["by_currency"]["IRR"]
    assert (s["last"], s["last_supplier"], s["lowest"], s["lowest_supplier"], s["highest"], s["count"]) == (
        51_000, "Beta Paper", 49_000, "Beta Paper", 55_000, 4)
    only_a = api.get("/purchase-orders/price-history", params={"q": "A4", "entity_id": a["id"]}).json()
    assert {x["supplier"] for x in only_a["rows"]} == {"Alpha Paper"}
    assert api.get("/purchase-orders/price-history").status_code == 422                # say what
    # a bill made from an order doesn't repeat the order's price
    po = _receive(api, _po(api, a, [("Envelopes", 100, 1_000)]), {"Envelopes": 100})
    api.post(f"/purchase-orders/{po['id']}/bill", json={})
    assert len(api.get("/purchase-orders/price-history", params={"q": "Envelopes"}).json()["rows"]) == 1


# --- the UI ------------------------------------------------------------------------------------------------------

def test_the_order_panel_is_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    for el in ("po-bill-btn", "po-issue-btn", "po-close-btn", "po-cancel-btn", "po-delete-btn", "po-bills"):
        assert f'id="{el}"' in html, el
    js = open("app/static/js/11-time-expenses-payroll.js", encoding="utf-8").read()
    for piece in ("'/purchase-orders/price-history?limit=20&q='", "'/bill'", "method: 'DELETE'", "billable_qty"):
        assert piece in js, piece
    text = i18n_text()
    for k in ("poBilledQty", "poBillBtn", "poIssueBtn", "poCloseBtn", "poCancelBtn", "poDeleteBtn", "poPriceHint",
              "poBillsLabel", "poBillVoided", "poUpdateFailed", "poCloseConfirm", "poCancelConfirm", "poDeleteConfirm",
              "poBillPrompt", "poBillFailed", "poBilled"):
        assert text.count(f"{k}:") == 4, k
