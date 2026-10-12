"""Pence in the ledger and on bank statements (docs/PLAN_PENCE.md, phase P2;
scenarios Q2–Q5).

A Barclays statement's £7.50 fee went into the books as £7, so after every row
was posted the balance check was still £1 out (phone QA, 2026-10-12). Pounds
now keep their pence through journal entries, base amounts, every report and
the statement review."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_statement_export import _company

BARCLAYS = (
    "Date,Description,Money out,Money in,Balance\n"
    "01/10/2026,HARTLEY PROPERTIES RENT,950.00,,24050.00\n"
    "11/10/2026,MONTHLY ACCOUNT FEE,7.50,,24042.50\n"
    "12/10/2026,ARIA DESIGN LTD,,600.00,24642.50\n"
)

# every report a UK company's pages read (the Q1 list): none may fail on pence
REPORTS = [
    "/reports/ledger-summary", "/reports/owner-dashboard", "/reports/cash-forecast", "/reports/tax-summary",
    "/summary", "/insights", "/commitments/summary", "/accounts/opening-balances",
    "/brain/ceo/report", "/brain/cfo/report", "/brain/audit/report",
    "/manager-reports/books/trial-balance", "/manager-reports/books/trial-balance-by-currency",
    "/manager-reports/books/general-journal", "/manager-reports/books/general-ledger",
    "/manager-reports/journal/entries", "/manager-reports/financial/balance-sheet",
    "/manager-reports/financial/balance-sheet-periods", "/manager-reports/financial/income-statement",
    "/manager-reports/financial/cash-flow", "/manager-reports/financial/cash-flow-periods",
    "/manager-reports/financial/uk/balance-sheet", "/manager-reports/financial/uk/profit-and-loss",
    "/manager-reports/financial/uk/comprehensive-income", "/manager-reports/financial/uk/cash-flow",
    "/manager-reports/financial/uk/changes-in-equity", "/manager-reports/operational/accounts-payable",
    "/manager-reports/operational/accounts-receivable", "/manager-reports/operational/debtor-creditor",
    "/manager-reports/operational/cash-bank-statement", "/manager-reports/close-pack/checklist",
    "/exports/transactions.csv", "/exports/transactions.xlsx", "/manager-reports/financial/export",
]


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "uk", "GBP")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _post(api, lines, *, currency=None, when="2026-10-11", desc="Bank charges"):
    body = {"date": when, "description": desc, "lines": lines}
    if currency:
        body["currency"] = currency
    return api.post("/transactions", json=body)


def _balance(db, cid, code):
    from app.db.tenant import use_company
    from app.services.statement_review import book_balance_as_of
    from datetime import date
    with use_company(cid):
        return book_balance_as_of(db, code, date(2026, 12, 31))


def test_q2_a_pound_with_pence_in_the_ledger(uk, db):
    api, cid = uk
    r = _post(api, [{"account_code": "8000", "debit": 7.5, "credit": 0},
                    {"account_code": "1200", "debit": 0, "credit": "7.50"}])
    assert r.status_code == 201, r.text
    lines = {ln["account_code"]: ln for ln in r.json()["lines"]}
    assert lines["8000"]["debit"] == 7.5 and lines["1200"]["credit"] == 7.5
    assert _balance(db, cid, "1200") == Decimal("-7.5")
    tb = api.get("/manager-reports/books/trial-balance").json()
    rows = {r["account_code"]: r for r in tb["rows"]} if isinstance(tb, dict) and "rows" in tb else None
    if rows:                                            # the shape the page reads
        assert rows["8000"]["debit_turnover"] == 7.5
    # whole amounts are still integers, as every client has always read them
    r2 = _post(api, [{"account_code": "8000", "debit": 950, "credit": 0},
                     {"account_code": "1200", "debit": 0, "credit": 950}], desc="Rent")
    assert r2.json()["lines"][0]["debit"] == 950 and isinstance(r2.json()["lines"][0]["debit"], int)


def test_q3_too_many_places_is_refused_in_words(uk, client, db):
    api, cid = uk
    r = _post(api, [{"account_code": "8000", "debit": "7.505", "credit": 0},
                    {"account_code": "1200", "debit": 0, "credit": "7.505"}])
    assert r.status_code == 400 and r.json()["detail"] == "The amount can have at most 2 decimal places in GBP."
    r = api.post("/transactions", json={"date": "2026-10-11", "description": "x", "lines": [
        {"account_code": "8000", "debit": 7.5, "credit": 0}, {"account_code": "1200", "debit": 0, "credit": 7}]})
    assert r.status_code == 400 and "must equal credits" in r.json()["detail"]


def test_q3_rials_have_no_decimals(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        r = _post(api, [{"account_code": "6112", "debit": "2500000.5", "credit": 0},
                        {"account_code": "1110", "debit": 0, "credit": "2500000.5"}], desc="اجاره")
        assert r.status_code == 400 and r.json()["detail"] == "The amount can't have decimals in IRR."
        assert api.post("/transactions", headers={"X-UI-Language": "fa"}, json={
            "date": "2026-10-11", "description": "x", "lines": [
                {"account_code": "6112", "debit": 0.5, "credit": 0},
                {"account_code": "1110", "debit": 0, "credit": 0.5}]}).json()["detail"] == "مبلغ به ⁨IRR⁩ اعشار ندارد."
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


def test_q4_a_statement_to_the_penny(uk, db):
    api, cid = uk
    _post(api, [{"account_code": "1200", "debit": 25_000, "credit": 0},
                {"account_code": "3000", "debit": 0, "credit": 25_000}], when="2026-09-01", desc="Capital")
    _post(api, [{"account_code": "7200", "debit": 950, "credit": 0},
                {"account_code": "1200", "debit": 0, "credit": 950}], when="2026-10-01", desc="Rent")
    r = api.post("/brain/bank-statements/upload", params={"bank_name": "Barclays"},
                 files={"file": ("barclays.csv", BARCLAYS.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    got = api.get(f"/brain/bank-statements/{sid}").json()
    fee = next(x for x in got["rows"] if "FEE" in x["description"])
    assert fee["debit"] == 7.5                                       # it was 7
    review = api.post(f"/brain/bank-statements/{sid}/review").json()
    bal = review["balance"]
    assert (bal["statement_closing"], bal["book_balance"]) == (24642.5, 24050)
    assert bal["gap"] == 592.5 and bal["unrecorded_net"] == 592.5 and bal["explained"] is True
    # post the two differences as the walk would: the books close to the penny
    _post(api, [{"account_code": "8000", "debit": 7.5, "credit": 0},
                {"account_code": "1200", "debit": 0, "credit": 7.5}], when="2026-10-11")
    _post(api, [{"account_code": "1200", "debit": 600, "credit": 0},
                {"account_code": "4000", "debit": 0, "credit": 600}], when="2026-10-12", desc="Aria")
    bal = api.post(f"/brain/bank-statements/{sid}/review").json()["balance"]
    assert bal is None or bal["gap"] == 0, bal


def test_q5_pence_across_currencies(uk, client, db):
    api, cid = uk
    assert api.post("/fx/rates", json={"from_currency": "EUR", "to_currency": "GBP", "rate": 0.85,
                                       "effective_date": "2026-10-01"}).status_code == 201
    r = _post(api, [{"account_code": "8000", "debit": "10.01", "credit": 0},
                    {"account_code": "1200", "debit": 0, "credit": "10.01"}], currency="EUR")
    assert r.status_code == 201, r.text
    from app.db.tenant import use_company
    from app.models.transaction import TransactionLine
    import uuid
    with use_company(cid):
        lines = db.query(TransactionLine).filter(TransactionLine.transaction_id == uuid.UUID(r.json()["id"])).all()
        assert sorted((ln.base_debit, ln.base_credit) for ln in lines) == [(0, Decimal("8.51")), (Decimal("8.51"), 0)]


def test_q5_a_rial_company_converts_dollars_with_cents(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        assert api.post("/fx/rates", json={"from_currency": "USD", "to_currency": "IRR", "rate": 600_000,
                                           "effective_date": "2026-10-01"}).status_code == 201
        r = _post(api, [{"account_code": "6112", "debit": "12.34", "credit": 0},
                        {"account_code": "1110", "debit": 0, "credit": "12.34"}], currency="USD", desc="subscription")
        assert r.status_code == 201, r.text
        from app.db.tenant import use_company
        from app.models.transaction import TransactionLine
        import uuid
        with use_company(cid):
            lines = db.query(TransactionLine).filter(TransactionLine.transaction_id == uuid.UUID(r.json()["id"])).all()
            assert sorted((ln.base_debit, ln.base_credit) for ln in lines) == [(0, 7_404_000), (7_404_000, 0)]
            assert all(type(ln.base_debit) is int for ln in lines)
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


def test_every_report_reads_pence(uk):
    """A UK company with pence in its books: every report its pages read
    answers (a Decimal meeting a float raised; an int() cut the pence)."""
    api, _ = uk
    _post(api, [{"account_code": "1200", "debit": "25000.25", "credit": 0},
                {"account_code": "3000", "debit": 0, "credit": "25000.25"}], when="2026-09-01", desc="Capital")
    _post(api, [{"account_code": "8000", "debit": "7.50", "credit": 0},
                {"account_code": "1200", "debit": 0, "credit": "7.50"}])
    _post(api, [{"account_code": "1200", "debit": "600.99", "credit": 0},
                {"account_code": "4000", "debit": 0, "credit": "600.99"}], when="2026-10-12", desc="Sale")
    # an old receivable with pence: the aging buckets past 30 days are money too
    _post(api, [{"account_code": "1100", "debit": "35.47", "credit": 0},
                {"account_code": "4000", "debit": 0, "credit": "35.47"}], when="2026-07-01", desc="Old sale")
    failed = {}
    for path in REPORTS:
        r = api.get(path)
        if r.status_code >= 500:
            failed[path] = r.status_code
    assert failed == {}, failed
    summary = api.get("/reports/ledger-summary").json()
    assert "7.5" in str(summary) or "7.50" in str(summary), summary
