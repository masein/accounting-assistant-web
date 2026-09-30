"""The debtors/creditors report and the cash-and-bank statement use the
chart's own accounts. Receivables were code 1112 and payables "21%" — the
Iranian chart: a UK company's report was empty (its debtors are 1100, its
creditors 2100), and "21%" also took in customer deposits, VAT and payroll
liabilities linked to a supplier. The bank statement defaulted to 1110."""
from __future__ import annotations

import pytest

from tests.test_statement_export import _company

CHART = {"uk": ("GBP", "1100", "2100", "4000", "5000", "1200", "2200"),
         "ir": ("IRR", "1112", "2110", "4110", "6112", "1110", "2130")}


@pytest.fixture(params=["uk", "ir"])
def co(request, client, db):
    from tests.test_admin_audit import _purge_company
    loc = request.param
    api, cid = _company(client, db, loc, CHART[loc][0])
    yield api, loc
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, ccy, lines, link):
    r = api.post("/transactions", json={"date": "2026-08-10", "description": "x", "currency": ccy, "lines": [
        {"account_code": c, "debit": d, "credit": k} for c, d, k in lines], "entity_links": [link]})
    assert r.status_code == 201, r.text


def test_debtors_and_creditors_on_the_companys_chart(co):
    api, loc = co
    ccy, ar, ap, sale, cost, _bank, vat = CHART[loc]
    _post(api, ccy, [(ar, 1_000, 0), (sale, 0, 1_000)], {"role": "client", "name": "Acme"})
    _post(api, ccy, [(cost, 400, 0), (ap, 0, 400)], {"role": "supplier", "name": "Paper Co"})
    _post(api, ccy, [(cost, 70, 0), (vat, 0, 70)], {"role": "supplier", "name": "Paper Co"})    # not a creditor movement
    rep = api.get("/manager-reports/operational/debtor-creditor",
                  params={"from_date": "2026-08-01", "to_date": "2026-08-31"}).json()
    assert [(r["entity_name"], r["total"]) for r in rep["debtors"]] == [("Acme", 1_000)], rep
    assert [(r["entity_name"], r["total"]) for r in rep["creditors"]] == [("Paper Co", 400)], rep   # not + the VAT
    assert rep["totals"] == {"debtors": 1_000, "creditors": 400}


def test_the_cash_and_bank_statement_defaults_to_the_charts_bank(co):
    api, loc = co
    ccy, _ar, _ap, sale, _cost, bank, _vat = CHART[loc]
    _post(api, ccy, [(bank, 250, 0), (sale, 0, 250)], {"role": "client", "name": "Walk-in"})
    r = api.get("/manager-reports/operational/cash-bank-statement",
                params={"from_date": "2026-08-01", "to_date": "2026-08-31"})
    assert r.status_code == 200, r.text
    assert "250" in str(r.json()), r.json()
