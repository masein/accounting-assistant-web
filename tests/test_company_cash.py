"""Cash is every cash and bank account the company has — the locale's (Iran
1110, UK 1200–1229) and each bank entity's own ledger account (Mellat 1111 …).

The locale's alone left an Iranian company's other banks out of the
dashboard's cash on hand (and runway, forecast, insights), and the generic
cash-flow reports only read codes starting "1110": a UK company's showed
nothing, an Iranian company's missed every bank but the first."""
from __future__ import annotations

import pytest

from tests.test_statement_export import _company

DAY = "2026-08-10"
CHART = {"uk": ("GBP", "1200", "4000"), "ir": ("IRR", "1110", "4110")}


@pytest.fixture(params=["uk", "ir"])
def co(request, client, db):
    from tests.test_admin_audit import _purge_company
    loc = request.param
    ccy, cash, sale = CHART[loc]
    api, cid = _company(client, db, loc, ccy)
    second = api.post("/entities", json={"type": "bank", "name": "Second Bank"}).json()["code"]

    def post(dr, cr, amount, desc):
        r = api.post("/transactions", json={"date": DAY, "description": desc, "currency": ccy, "lines": [
            {"account_code": dr, "debit": amount, "credit": 0}, {"account_code": cr, "debit": 0, "credit": amount}]})
        assert r.status_code == 201, r.text
    post(cash, sale, 1_000, "sale into the main account")
    post(second, sale, 500, "sale into the second bank")
    post(second, cash, 200, "transfer between our own accounts")      # not a cash flow
    yield api, cid, loc, cash, second
    client.cookies.clear()
    _purge_company(db, cid)


def test_the_predicate(co, db):
    from app.db.tenant import use_company
    from app.services.cash_service import company_cash_predicate
    _api, cid, loc, cash, second = co
    with use_company(cid):
        is_cash = company_cash_predicate(db)
    assert is_cash(cash) and is_cash(second) and not is_cash(CHART[loc][2]) and not is_cash("")


def test_cash_on_hand_counts_every_bank(co, db):
    from app.db.tenant import use_company
    from app.services.cash_service import cash_on_hand
    api, cid, loc, _cash, _second = co
    with use_company(cid):
        assert cash_on_hand(db, locale=loc) == 1_500
    kpis = {k["key"]: k["value"] for k in api.get("/reports/owner-dashboard").json()["kpis"]}
    assert kpis["cash_on_hand"] == 1_500


def test_the_cash_flow_reports_count_every_bank(co):
    api, _cid, _loc, _cash, _second = co
    window = {"from_date": "2026-08-01", "to_date": "2026-08-31"}
    cf = api.get("/manager-reports/financial/cash-flow", params=window).json()
    assert cf["totals"]["net_cash_change"] == 1_500, cf["totals"]
    per = api.get("/manager-reports/financial/cash-flow-periods", params=window).json()
    assert per["totals"]["total_inflow"] == 1_500 and per["totals"]["total_outflow"] == 0, per["totals"]
    assert all("transfer" not in t["description"] for p in per["periods"] for t in p["transactions"])
