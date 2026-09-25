"""Manager reports over HTTP (roadmap 2026-09 §6, suite 6): the 26 routes
no test touched, in a private Iranian company with a real chart. Running
balances tie to the trial balance, soft-deleted entries and voided or draft
invoices stay out of every report, CSV exports carry the same rows, and the
aging reports age by due date as of the report date."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta

import pytest

TODAY = date.today()


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def co(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty
    from app.db.tenant import use_company
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Reports Co", slug=f"rep-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf), cid
    client.cookies.clear()
    _purge_company(db, cid)


def _txn(api, when, lines, desc="entry", links=None, currency="IRR"):
    r = api.post("/transactions", json={"date": when, "description": desc, "currency": currency,
                                        "lines": lines, "entity_links": links or []})
    assert r.status_code == 201, r.text
    return r.json()


def _l(code, dr=0, cr=0):
    return {"account_code": code, "debit": dr, "credit": cr}


@pytest.fixture()
def books(co):
    """Three live entries, one soft-deleted, two parties."""
    api, cid = co
    client_ent = api.post("/entities", json={"type": "client", "name": "Mehr Co"}).json()
    supplier = api.post("/entities", json={"type": "supplier", "name": "Pars Supply"}).json()
    sale = _txn(api, _d(20), [_l("1110", dr=5_000_000), _l("4110", cr=5_000_000)], "cash sale",
                [{"entity_id": client_ent["id"], "role": "client"}])
    expense = _txn(api, _d(10), [_l("6112", dr=1_200_000), _l("1110", cr=1_200_000)], "supplies",
                   [{"entity_id": supplier["id"], "role": "supplier"}])
    capital = _txn(api, _d(5), [_l("1110", dr=10_000_000), _l("3110", cr=10_000_000)], "capital")
    on_credit = _txn(api, _d(4), [_l("1112", dr=3_000_000), _l("4110", cr=3_000_000)], "credit sale",
                     [{"entity_id": client_ent["id"], "role": "client"}])
    ghost = _txn(api, _d(3), [_l("1112", dr=999_999), _l("4110", cr=999_999)], "deleted later",
                 [{"entity_id": client_ent["id"], "role": "client"}])
    assert api.delete(f"/transactions/{ghost['id']}").status_code == 204
    return {"api": api, "cid": cid, "client": client_ent, "supplier": supplier,
            "ids": {"sale": sale["id"], "expense": expense["id"], "capital": capital["id"],
                    "on_credit": on_credit["id"], "ghost": ghost["id"]}}


P = {"from_date": _d(60), "to_date": TODAY.isoformat()}


# ─── books ─────────────────────────────────────────────────────────────

def test_general_journal_lists_live_entries_only_and_exports_csv(books):
    api, ids = books["api"], books["ids"]
    j = api.get("/manager-reports/books/general-journal", params=P).json()
    got = {str(i["transaction_id"]) for i in j["items"]}
    assert got == {ids["sale"], ids["expense"], ids["capital"], ids["on_credit"]}
    raw = api.get("/manager-reports/books/general-journal", params={**P, "format": "csv"})
    assert raw.status_code == 200 and "text/csv" in raw.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(raw.text.lstrip("﻿"))))
    assert {r["transaction_id"] for r in rows} == got and len(rows) == 8
    assert api.get("/manager-reports/journal/entries", params=P).json()["total"] == 4


def test_account_ledger_running_balance_ties_to_the_trial_balance(books):
    api = books["api"]
    led = api.get("/manager-reports/books/account-ledger/1110", params=P).json()
    assert [int(i["debit"]) - int(i["credit"]) for i in led["items"]] == [5_000_000, -1_200_000, 10_000_000]
    assert int(led["items"][-1]["running_balance"]) == 13_800_000
    tb = api.get("/manager-reports/books/trial-balance", params=P).json()
    bank = next(r for r in tb["rows"] if r["account_code"] == "1110")
    assert int(bank["debit_balance"]) - int(bank["credit_balance"]) == 13_800_000
    assert sum(int(r["debit_turnover"]) for r in tb["rows"]) == sum(int(r["credit_turnover"]) for r in tb["rows"])
    gl = api.get("/manager-reports/books/general-ledger", params=P).json()
    assert {r["account_code"] for r in gl["rows"]} >= {"1110", "4110", "6112", "3110"}
    for fmt_url in ("/manager-reports/books/account-ledger/1110", "/manager-reports/books/trial-balance",
                    "/manager-reports/books/general-ledger"):
        assert "text/csv" in api.get(fmt_url, params={**P, "format": "csv"}).headers["content-type"]


def test_cash_bank_statement_and_person_running_balance(books):
    api, client_ent = books["api"], books["client"]
    st = api.get("/manager-reports/operational/cash-bank-statement",
                 params={**P, "account_code": "1110"}).json()
    assert len(st["rows"]) == 3 and st["total"] == 3              # the deleted entry is not on the statement
    assert int(st["rows"][-1]["running_balance"]) == 13_800_000
    prb = api.get("/manager-reports/operational/person-running-balance",
                  params={**P, "entity_id": client_ent["id"], "role": "client"})
    assert prb.status_code == 200, prb.text
    body = prb.json()
    assert [r["running_balance"] for r in body["rows"]] == [3_000_000]      # the deleted entry is not there
    assert body["closing_balance"] == 3_000_000 and "999999" not in prb.text
    assert api.get("/manager-reports/operational/person-running-balance",
                   params={**P, "entity_id": str(uuid.uuid4()), "role": "client"}).status_code == 404


def test_trial_balance_by_currency_keeps_currencies_apart(books):
    api = books["api"]
    _txn(api, _d(2), [_l("1110", dr=300), _l("4110", cr=300)], "usd sale", currency="USD")
    r = api.get("/manager-reports/books/trial-balance-by-currency", params=P).json()
    blocks = {b["currency"]: b for b in r["blocks"]}
    assert set(blocks) >= {"IRR", "USD"}
    usd_bank = next(x for x in blocks["USD"]["rows"] if x["account_code"] == "1110")
    assert int(usd_bank["debit_balance"]) == 300


def test_accounts_list_and_search_helpers(books):
    api = books["api"]
    accounts = api.get("/manager-reports/accounts/list").json()
    codes = {a["code"] for a in accounts}
    assert "1110" in codes and "11" not in codes                 # groups are not postable
    found = api.get("/manager-reports/entities/search", params={"search": "mehr"}).json()
    assert [e["name"] for e in found] == ["Mehr Co"]
    assert [e["name"] for e in api.get("/manager-reports/entities/search", params={"type": "supplier"}).json()] == ["Pars Supply"]


def test_journal_reverse_posts_a_balanced_mirror(books):
    api, ids = books["api"], books["ids"]
    r = api.post(f"/manager-reports/journal/{ids['expense']}/reverse", params={"reverse_date": TODAY.isoformat()})
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert sum(int(x["debit"]) for x in lines) == sum(int(x["credit"]) for x in lines) == 1_200_000
    led = api.get("/manager-reports/books/account-ledger/6112", params=P).json()
    assert sum(int(i["debit"]) - int(i["credit"]) for i in led["items"]) == 0
    assert api.post(f"/manager-reports/journal/{uuid.uuid4()}/reverse").status_code == 404


def test_cash_flow_periods_respond_per_granularity(books):
    api = books["api"]
    for g in ("weekly", "monthly", "quarterly", "seasonal"):
        r = api.get("/manager-reports/financial/cash-flow-periods", params={**P, "granularity": g})
        assert r.status_code == 200 and r.json()["periods"], g
    assert api.get("/manager-reports/financial/cash-flow-periods", params={"granularity": "hourly"}).status_code == 422


# ─── invoices: aging and sales ─────────────────────────────────────────

def _inv(api, entity, *, kind="sales", status="issued", issued, due, amount=1_000_000, product="Consulting"):
    r = api.post("/invoices", json={
        "number": f"R-{uuid.uuid4().hex[:6]}", "kind": kind, "status": status, "issue_date": issued,
        "due_date": due, "amount": amount, "currency": "IRR", "entity_id": entity["id"],
        "items": [{"product_name": product, "quantity": 1, "unit_price": amount}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_receivables_aging_by_due_date_as_of_the_report_date(books):
    api, c = books["api"], books["client"]
    old = _inv(api, c, issued=_d(120), due=_d(100))              # issued long before this month
    d45 = _inv(api, c, issued=_d(75), due=_d(45))
    d10 = _inv(api, c, issued=_d(40), due=_d(10))
    fresh = _inv(api, c, issued=_d(2), due=(TODAY + timedelta(days=28)).isoformat())
    part = _inv(api, c, issued=_d(40), due=_d(20), amount=2_000_000)
    api.post(f"/invoices/{part['id']}/payments", json={"amount": 500_000, "method": "bank"})
    draft = _inv(api, c, status="draft", issued=_d(40), due=_d(30))
    paid = _inv(api, c, issued=_d(40), due=_d(30))
    api.post(f"/invoices/{paid['id']}/payments", json={"amount": 1_000_000, "method": "bank"})

    ar = api.get("/manager-reports/operational/accounts-receivable").json()   # no dates: as of today
    rows = {x["invoice_number"]: x for x in ar["items"]}
    assert old["number"] in rows                                   # not limited to this month's invoices
    assert draft["number"] not in rows and paid["number"] not in rows
    assert rows[old["number"]]["aging_bucket"] == "90+"
    assert rows[d45["number"]]["aging_bucket"] == "31-60"
    assert rows[d10["number"]]["aging_bucket"] == "1-30"
    assert rows[fresh["number"]]["aging_bucket"] == "current"
    assert rows[part["number"]]["balance_due"] == 1_500_000
    assert ar["total"] == sum(x["balance_due"] for x in ar["items"])


def test_payables_aging(books):
    api, s = books["api"], books["supplier"]
    bill = _inv(api, s, kind="purchase", issued=_d(70), due=_d(65))
    ap = api.get("/manager-reports/operational/accounts-payable").json()
    row = next(x for x in ap["items"] if x["invoice_number"] == bill["number"])
    assert row["vendor"] == "Pars Supply" and row["aging_bucket"] == "61-90"


def test_debtor_creditor_excludes_deleted_entries(books):
    api = books["api"]
    dc = api.get("/manager-reports/operational/debtor-creditor", params=P)
    assert dc.status_code == 200 and "999999" not in dc.text


def test_sales_and_purchase_reports_ignore_voided_and_draft_invoices(books):
    api, c, s = books["api"], books["client"], books["supplier"]
    live = _inv(api, c, issued=_d(5), due=_d(0), amount=700_000, product="Widget")
    voided = _inv(api, c, issued=_d(5), due=_d(0), amount=300_000, product="Widget")
    assert api.post(f"/invoices/{voided['id']}/void").status_code == 200
    _inv(api, c, status="draft", issued=_d(5), due=_d(0), amount=50_000, product="Widget")
    bill = _inv(api, s, kind="purchase", issued=_d(5), due=_d(0), amount=400_000, product="Steel")

    by_product = api.get("/manager-reports/sales/by-product", params=P).json()
    widget = next(r for r in by_product["rows"] if r["product_name"] == "Widget")
    assert int(widget["sales_amount"]) == 700_000
    by_invoice = api.get("/manager-reports/sales/by-invoice", params=P).json()
    assert {r["invoice_number"] for r in by_invoice["rows"]} == {live["number"]}
    trend = api.get("/manager-reports/sales/trend", params={**P, "product_name": "widget"}).json()
    assert trend["totals"]["total_sales"] == 700_000 and trend["totals"]["total_invoices"] == 1
    pur = api.get("/manager-reports/purchases/by-invoice", params=P).json()
    assert [r["invoice_number"] for r in pur["rows"]] == [bill["number"]]
    pp = api.get("/manager-reports/purchases/by-product", params=P).json()
    assert [int(r["sales_amount"]) for r in pp["rows"] if r["product_name"] == "Steel"] == [400_000]
    assert pp["totals"]["purchase_amount"] == 400_000
    assert "Widget" in api.get("/manager-reports/products/names").json()


# ─── inventory ─────────────────────────────────────────────────────────

def test_inventory_items_movements_balance_and_price(co):
    api, _ = co
    item = api.post("/manager-reports/inventory/items", json={"name": "Cement bag", "sku": "CEM-1", "list_price": 900})
    assert item.status_code == 201, item.text
    iid = item.json()["id"]
    assert item.json()["list_price"] == 900                    # used to be dropped on create
    assert api.post("/manager-reports/inventory/items", json={"name": "Bad", "list_price": -1}).status_code == 422
    assert api.post("/manager-reports/inventory/items", json={"name": ""}).status_code == 422
    for body in ({"movement_type": "IN", "quantity": 10, "unit_cost": 700},
                 {"movement_type": "OUT", "quantity": 4, "unit_cost": 700}):
        r = api.post("/manager-reports/inventory/movements",
                     json={"item_id": iid, "movement_date": _d(1), **body})
        assert r.status_code == 201, r.text
    assert api.post("/manager-reports/inventory/movements", json={"item_id": iid, "movement_date": _d(1),
                                                                 "movement_type": "LOST", "quantity": 1}).status_code in (400, 422)
    moves = api.get("/manager-reports/inventory/movements", params={**P, "item_id": iid}).json()
    assert len(moves["rows"]) == 2 and moves["total"] == 2
    bal = api.get("/manager-reports/inventory/balance").json()
    row = next(r for r in bal["rows"] if str(r["item_id"]) == iid)
    assert float(row["on_hand_qty"]) == 6 and row["item_name"] == "Cement bag"
    assert [i["name"] for i in api.get("/manager-reports/inventory/items").json()] == ["Cement bag"]
    p = api.patch(f"/manager-reports/inventory/items/{iid}/price", params={"list_price": 950}).json()
    assert p["old_price"] == 900 and p["new_price"] == 950
    assert api.patch(f"/manager-reports/inventory/items/{uuid.uuid4()}/price", params={"list_price": 1}).status_code == 404
    assert api.patch(f"/manager-reports/inventory/items/{iid}/price", params={"list_price": -5}).status_code == 422
