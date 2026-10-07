"""Each module against the books and the reports (scenarios M1–M6, 2026-10-07).

Every module had its own tests; the bugs that reached CFO Mode and the
dashboard this month sat between them — a figure right in its module and
wrong once a report read it. Here each module runs a realistic month through
the API, then the module's own figures, the ledger, the statements and the
dashboard must say the same, against numbers worked out by hand.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta

import pytest

from app.db.tenant import use_company


@pytest.fixture()
def ir(client, db):
    """A private Iranian company with its chart and an owner."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Crosscheck Co", slug=f"xc-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner", company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield {"api": _CSRFTestClient(client, csrf), "cid": cid}
    client.cookies.clear()
    _purge_company(db, cid)


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


def _gl(db, co, code):
    """Debit − credit of an account and its sub-accounts, to date, at base value."""
    from app.services.cfo_intelligence import ledger_balance
    with use_company(co["cid"]):
        return ledger_balance(db, (code,), None, date.today())


def _trial_balanced(co):
    tb = _ok(co["api"].get("/manager-reports/books/trial-balance", params={"page_size": 1000}))
    rows = tb["rows"]
    assert sum(r["debit_balance"] for r in rows) == sum(r["credit_balance"] for r in rows)


def _csv_total(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    return next(r for r in rows if r["employee_name"] == "TOTAL")


# --- M1: a month of Iranian payroll ------------------------------------------------------------------------

def test_m1_a_payroll_month_reaches_the_books_the_lists_and_the_reports(ir, db):
    """Two employees under the 1405 rules: A on 200,000,000 with no children,
    B on 500,000,000 with one child. Worked by hand:

      A  gross 252,000,000  insurance 17,640,000  employer 57,960,000  tax 0           net 234,360,000
      B  gross 568,625,550  insurance 38,640,000  employer 126,960,000 tax 12,998,555  net 516,986,995

    (B: the child allowance 16,625,550 isn't insurable; taxable 529,985,550,
    10% of the 129,985,550 above 400,000,000.)"""
    from app.services.payroll_rules import seed_payroll_rules
    with use_company(ir["cid"]):
        seed_payroll_rules(db)
        db.commit()
    api = ir["api"]
    people = {}
    for name, base, kids, nid in (("Ali Rezaei", 200_000_000, 0, "0012345678"), ("Maryam Kazemi", 500_000_000, 1, "0087654321")):
        e = _ok(api.post("/entities", json={"type": "employee", "name": name, "national_id": nid, "code": f"E-{nid[-2:]}",
                                            "iban": "IR820540102680020817909002", "bank_name": "Bank Melli"}), 201)
        _ok(api.post("/payroll/profiles", json={"entity_id": e["id"], "pay_type": "salaried", "base_salary": base,
                                                "tax_mode": "statutory", "children": kids}), 201)
        people[name] = e["id"]
    run = _ok(api.post("/payroll/runs", json={"period_start": "2026-08-23", "period_end": "2026-09-22",
                                              "pay_date": "2026-09-22"}), 201)
    lines = {ln["employee_name"]: ln for ln in run["lines"]}
    got = {n: (ln["gross"], ln["social_security"], ln["employer_social"], ln["income_tax"], ln["net_pay"])
           for n, ln in lines.items()}
    assert got == {"Ali Rezaei": (252_000_000, 17_640_000, 57_960_000, 0, 234_360_000),
                   "Maryam Kazemi": (568_625_550, 38_640_000, 126_960_000, 12_998_555, 516_986_995)}
    assert (run["total_gross"], run["total_net"]) == (820_625_550, 751_346_995)

    _ok(api.post(f"/payroll/runs/{run['id']}/post"))
    _ok(api.post(f"/payroll/runs/{run['id']}/pay"))

    # the ledger: wages and the employer's share as costs; what is owed to the tax office and to insurance
    assert _gl(db, ir, "6110") == 820_625_550
    assert _gl(db, ir, "6111") == 184_920_000
    assert _gl(db, ir, "2160") == -12_998_555
    assert _gl(db, ir, "2170") == -(56_280_000 + 184_920_000)
    assert _gl(db, ir, "2180") == 0                                       # net pay paid out
    assert _gl(db, ir, "1110") == -751_346_995
    _trial_balanced(ir)

    # the lists for the insurance and tax offices say the same as the ledger
    ins = _csv_total(api.get(f"/payroll/runs/{run['id']}/insurance-list.csv").text)
    assert (int(ins["employee_share"]), int(ins["employer_share"]), int(ins["total_premium"])) == \
        (56_280_000, 184_920_000, 241_200_000)
    tax = _csv_total(api.get(f"/payroll/runs/{run['id']}/tax-list.csv").text)
    assert (int(tax["income_tax"]), int(tax["net_pay"])) == (12_998_555, 751_346_995)

    # the year summary, the income statement and the dashboard
    year = _ok(api.get("/payroll/year-summary", params={"year": 1405}))
    assert sum(e["gross"] for e in year["employees"]) == 820_625_550
    stmt = _ok(api.get("/manager-reports/financial/iran/income-statement",
                       params={"from_date": "2026-08-23", "to_date": "2026-09-22"}))
    sga = next(r for r in stmt["rows"] if r["key"] == "opex_sga")
    assert abs(sga["amount_current"]) == 820_625_550 + 184_920_000
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    dash = _ok(api.get("/reports/owner-dashboard", params={"currency": "IRR"}))
    kpi = {k["key"]: k["value"] for k in dash["kpis"]}
    assert kpi["tax_and_liability_payable"] == 12_998_555 + 241_200_000


# --- M3: a fixed asset bought, depreciated and sold ------------------------------------------------------------

def _months_ago(n):
    return date.today() - timedelta(days=31 * n)


def test_m3_fixed_assets_agree_with_the_ledger_and_the_balance_sheet(ir, db):
    """A computer of 36,000,000 over 36 months is 1,000,000 a month; desks of
    6,000,000 over 60 months are 100,000 a month, sold today for 5,000,000."""
    api = ir["api"]
    pc = _ok(api.post("/fixed-assets", json={"name": "Workstation", "category": "computer", "cost": 36_000_000,
                                             "acquired_on": _months_ago(8).isoformat(), "acquisition": "bank"}), 201)
    desks = _ok(api.post("/fixed-assets", json={"name": "Desks", "category": "furniture", "cost": 6_000_000,
                                                "acquired_on": _months_ago(5).isoformat(), "acquisition": "bank"}), 201)
    assert (pc["monthly_charge"], desks["monthly_charge"]) == (1_000_000, 100_000)
    _ok(api.post("/fixed-assets/depreciation-run", json={}))
    again = _ok(api.post("/fixed-assets/depreciation-run", json={}))
    assert again["journals"] == []                                        # never twice

    reg = _ok(api.get("/fixed-assets"))
    rows = {a["name"]: a for a in reg["assets"]}
    acc_pc, acc_desks = rows["Workstation"]["accumulated"], rows["Desks"]["accumulated"]
    assert acc_pc > 0 and acc_pc % 1_000_000 == 0 and acc_desks % 100_000 == 0
    # the register, the ledger and the balance sheet are the same figures
    assert _gl(db, ir, "1210") == 42_000_000
    assert _gl(db, ir, "1219") == -(acc_pc + acc_desks)
    assert _gl(db, ir, "6120") == acc_pc + acc_desks
    assert reg["totals"]["IRR"]["net_book_value"] == 42_000_000 - acc_pc - acc_desks

    # sold today: the months before this one are depreciated first; then the gain or loss
    sale = _ok(api.post(f"/fixed-assets/{desks['id']}/dispose", json={"on": date.today().isoformat(), "proceeds": 5_000_000}))
    nbv = 6_000_000 - sale["accumulated"]
    assert (sale["gain"], sale["loss"]) == ((5_000_000 - nbv, 0) if 5_000_000 >= nbv else (0, nbv - 5_000_000))
    assert _gl(db, ir, "1210") == 36_000_000
    assert _gl(db, ir, "1219") == -rows["Workstation"]["accumulated"]
    assert _gl(db, ir, "1110") == -42_000_000 + 5_000_000
    assert _gl(db, ir, "4310") == -sale["gain"] and _gl(db, ir, "6220") == sale["loss"]
    reg2 = _ok(api.get("/fixed-assets"))
    assert reg2["totals"]["IRR"]["net_book_value"] == 36_000_000 - rows["Workstation"]["accumulated"]
    _trial_balanced(ir)
    bs = _ok(api.get("/manager-reports/financial/iran/balance-sheet"))
    total = {r["key"]: r for r in bs["rows"]}
    assert total["total_assets"]["amount_current"] == total["total_equity_and_liabilities"]["amount_current"]


# --- M4, M5: a UK quarter — the VAT return and the income tax update -------------------------------------------

@pytest.fixture()
def uk(client, db):
    """A private UK company (GBP) with its chart and an owner."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Thames Check Ltd", slug=f"tc-{uuid.uuid4().hex[:6]}", locale="uk",
                base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="uk")
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner", company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield {"api": _CSRFTestClient(client, csrf), "cid": cid}
    client.cookies.clear()
    _purge_company(db, cid)


def _uk_invoice(co, number, kind, items, on):
    who = _ok(co["api"].post("/entities", json={"type": "client" if kind == "sales" else "supplier",
                                                "name": f"{number} party"}), 201)
    return _ok(co["api"].post("/invoices", json={
        "number": number, "kind": kind, "status": "issued", "issue_date": on, "due_date": on, "amount": 0,
        "currency": "GBP", "entity_id": who["id"], "items": items}), 201)


def test_m4_m5_a_uk_quarter_agrees_with_the_vat_return_and_the_income_tax_update(uk, db):
    """July–September 2026 (VAT stagger 1; ITSA 2026-27 quarter 2, 6 Jul – 5 Oct):
    a sale of 1,000 + 20% VAT, a zero-rated sale of 300, a purchase of 400 + 20%,
    and a credit note of 120 (100 + 20 VAT) on the first sale. By hand:
    box 1 = 200 − 20 = 180, box 4 = 80, box 5 = 100 payable, box 6 = 1,000 + 300 − 100
    = 1,200, box 7 = 400; turnover 1,200, cost of goods 400, profit 800."""
    api = uk["api"]
    sale = _uk_invoice(uk, "TS-1", "sales", [{"product_name": "Design", "unit_price": 1_000, "tax_rate": 20}], "2026-08-10")
    _uk_invoice(uk, "TS-2", "sales", [{"product_name": "Books", "unit_price": 300, "tax_rate": 0,
                                       "tax_treatment": "zero_rated"}], "2026-08-12")
    _uk_invoice(uk, "PB-1", "purchase", [{"product_name": "Paper", "unit_price": 400, "tax_rate": 20}], "2026-08-15")
    _ok(api.post(f"/invoices/{sale['id']}/credit-notes", json={"amount": 120, "date": "2026-08-20"}), 201)

    # M4: the nine boxes, and the VAT accounts the invoices posted to
    vat = _ok(api.get("/tax/uk/vat/return", params={"period_end": "2026-09-30"}))
    boxes = {k: vat["boxes"][k] for k in ("1", "3", "4", "5", "6", "7")}
    assert boxes == {"1": 180, "3": 180, "4": 80, "5": 100, "6": 1_200, "7": 400}, vat["boxes"]
    assert vat["direction"] == "payable"
    assert -_gl(db, uk, "2200") == boxes["1"]                 # output VAT owed, as the ledger has it
    assert _gl(db, uk, "1400") == boxes["4"]                  # input VAT to reclaim
    _trial_balanced(uk)

    # M5: the quarterly update, from the ledger, against the income statement
    _ok(api.put("/tax/uk/settings", json={"income_source": "self_employment"}))
    upd = _ok(api.get("/tax/uk/itsa/update", params={"tax_year": "2026-27", "quarter": 2}))
    q = upd["totals"]["quarter"]
    assert (q["income"], q["expenses"], q["profit"]) == (1_200, 400, 800), upd["totals"]
    pl = _ok(api.get("/manager-reports/financial/uk/profit-and-loss",
                     params={"from_date": upd["quarter"]["start"], "to_date": upd["quarter"]["end"]}))
    rows = {r["key"]: r["amount_current"] for r in pl["rows"]}
    assert (rows["gross_profit"], rows["profit_for_year"]) == (800, 800), rows


# --- M6: a personal month — the report card and net worth -------------------------------------------------------

@pytest.fixture()
def sara(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.services.company_service import provision_company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    name = f"sara-{uuid.uuid4().hex[:6]}"
    company, user = provision_company(db, name=f"Sara {name}", locale="ir", base_currency="IRR", username=name,
                                      password="personalpass123", kind="personal")
    db.commit()
    cid = str(company.id)
    tok = create_session_token(user_id=str(user.id), username=name, is_admin=False, role="personal", company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield {"api": _CSRFTestClient(client, csrf), "cid": cid}
    client.cookies.clear()
    _purge_company(db, cid)


def test_m6_a_personal_month_adds_up_on_the_report_card_and_in_net_worth(sara, db):
    """Shahrivar 1405 (23 Aug – 22 Sep 2026): salary 120,000,000; food
    30,000,000, rent 50,000,000, transport 10,000,000 — 90,000,000 spent, 30,000,000
    saved (25%). 20,000,000 of it bought 10 g of gold, worth 2,500,000 a gram at
    month end: net worth 10,000,000 in the bank + 25,000,000 of gold."""
    api = sara["api"]
    for day, dr, cr, amount, what in (("2026-08-24", "1110", "4110", 120_000_000, "حقوق"),
                                      ("2026-08-26", "6110", "1110", 30_000_000, "خرید ماه"),
                                      ("2026-08-27", "6120", "1110", 50_000_000, "اجاره"),
                                      ("2026-09-02", "6130", "1110", 10_000_000, "اسنپ"),
                                      ("2026-09-10", "1130", "1110", 20_000_000, "خرید طلا")):
        _ok(api.post("/transactions", json={"date": day, "description": what, "currency": "IRR",
                                            "lines": [{"account_code": dr, "debit": amount, "credit": 0},
                                                      {"account_code": cr, "debit": 0, "credit": amount}]}), 201)
    _ok(api.post("/personal/holdings", json={"account_code": "1130", "unit": "GOLDG", "quantity": 10, "label": "طلا"}), 201)
    _ok(api.post("/fx/rates", json={"from_currency": "GOLDG", "to_currency": "IRR", "rate": 2_500_000,
                                    "effective_date": "2026-09-20"}), 201)

    card = _ok(api.get("/personal/report-card", params={"month": "1405-06", "lang": "fa"}))
    assert (card["income"], card["spending"], card["saved"]) == (120_000_000, 90_000_000, 30_000_000)
    assert card["savings_rate"] == 25.0
    assert sum(c["amount"] for c in card["categories"]) == 90_000_000

    nw = _ok(api.get("/personal/net-worth", params={"as_of": "2026-09-22", "trend": "false"}))
    assert (nw["total_assets"], nw["net_worth"], nw["unrealized_gain"]) == (35_000_000, 35_000_000, 5_000_000), nw
    assert card["net_worth"]["end"] == nw["net_worth"], (card["net_worth"], nw["net_worth"])


# --- M2: inventory costing — the two methods, and the reports that show them ----------------------------------

def test_m2_inventory_reports_agree_under_both_methods(ir):
    """In 10 at 100, in 10 at 200, out 15. Weighted average: 150 a unit, COGS
    2,250, 5 left worth 750. FIFO: COGS 10×100 + 5×200 = 2,000, 5 left worth 1,000.
    The stock pages must say the same under each method. (Stock doesn't reach
    the ledger: movements aren't posted — see M2 in docs/qa/SCENARIOS.md.)"""
    api = ir["api"]
    item = _ok(api.post("/manager-reports/inventory/items", json={"name": "کاغذ A4", "sku": "A4-80", "unit": "box"}), 201)
    for kind, qty, cost, day in (("IN", 10, 100, "2026-08-01"), ("IN", 10, 200, "2026-08-10"), ("OUT", 15, 0, "2026-08-20")):
        _ok(api.post("/manager-reports/inventory/movements", json={
            "item_id": item["id"], "movement_date": day, "movement_type": kind, "quantity": qty, "unit_cost": cost}), 201)
    for method, value, cogs in (("weighted_average", 750, 2_250), ("fifo", 1_000, 2_000)):
        _ok(api.put("/manager-reports/inventory/settings", json={"method": method}))
        bal = _ok(api.get("/manager-reports/inventory/balance", params={"to_date": "2026-09-30"}))
        val = _ok(api.get("/manager-reports/inventory/valuation", params={"as_of": "2026-09-30"}))
        assert (bal["totals"]["inventory_value"], bal["totals"]["cogs"]) == (value, cogs), (method, bal["totals"])
        assert (val["totals"]["value"], val["totals"]["cogs"]) == (value, cogs), (method, val["totals"])
        assert val["other_method"]["value"] == (1_000 if method == "weighted_average" else 750)
