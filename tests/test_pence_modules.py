"""The other modules to the penny (docs/PLAN_PENCE.md, phase P4; scenarios Q9, Q10).

Payroll, mileage, fixed assets, installments, equity, budgets, recurring rules
and the settings' thresholds kept whole units: a £12.21 hourly rate was paid
at £12, 123 miles at 45p came to £55, a £100 loan in three parts was £34 + £33
+ £33. Pounds keep their pence; rials are computed exactly as before."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_statement_export import _company


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "uk", "GBP")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _entity(api, kind, name):
    r = api.post("/entities", json={"type": kind, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_q9_a_payslip_to_the_penny(uk):
    api, _ = uk
    emp = _entity(api, "employee", "Sam Hartley")
    r = api.post("/payroll/profiles", json={"entity_id": emp, "pay_type": "hourly", "hourly_rate": "12.21",
                                            "standard_hours": 162.5, "income_tax_rate": 0.2,
                                            "social_security_rate": 0.08})
    assert r.status_code == 201, r.text
    assert r.json()["hourly_rate"] == 12.21                                   # it was saved as 12
    run = api.post("/payroll/runs", json={"period_start": "2026-09-01", "period_end": "2026-09-30",
                                          "pay_date": "2026-09-30",
                                          "employees": [{"entity_id": emp, "hours": 162.5}]})   # 37.5 h × 52 / 12
    assert run.status_code == 201, run.text
    ln = run.json()["lines"][0]
    assert ln["gross"] == 1984.13                                             # 1,984.125 half-up
    assert (ln["income_tax"], ln["social_security"]) == (396.83, 158.73)      # 396.826, 158.7304
    assert ln["net_pay"] == 1428.57 and run.json()["total_net"] == 1428.57
    posted = api.post(f"/payroll/runs/{run.json()['id']}/post")
    assert posted.status_code == 200, posted.text
    slip = api.get(f"/payroll/runs/{run.json()['id']}/payslip/{emp}").json()
    assert (slip["gross"], slip["income_tax"], slip["net_pay"]) == (1984.13, 396.83, 1428.57)
    tb = {x["account_code"]: x for x in api.get("/reports/ledger-summary").json()["rows"]}
    assert tb["7100"]["debit_turnover"] == 1984.13                            # the posting agrees


def test_q10_mileage_at_45p(uk):
    api, _ = uk
    assert api.post("/expenses/settings", json={"mileage_rate": 0.45, "mileage_unit": "mi"}).status_code == 200
    assert api.get("/expenses/settings").json()["mileage_unit"] == "mile"     # what the page's picker offers
    assert api.post("/expenses/settings", json={"mileage_unit": "furlong"}).status_code == 422
    r = api.post("/expenses/mileage", json={"employee_name": "Sam Hartley", "claim_date": "2026-10-01",
                                            "distance": 123, "purpose": "Client visit"})
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 55.35                                        # it was £55


def test_installments_assets_and_dividends_split_to_the_penny(uk):
    api, _ = uk
    r = api.post("/commitments/installments", json={"title": "Van loan", "total_amount": 100, "count": 3,
                                                     "first_due": "2026-11-01"})
    assert r.status_code == 201, r.text
    assert [x["amount"] for x in r.json()] == [33.34, 33.33, 33.33]           # the first takes the odd penny
    r = api.post("/commitments/cheques", json={"title": "Office World", "amount": "120.50", "due_date": "2026-11-15"})
    assert r.status_code == 201 and r.json()["amount"] == 120.5, r.text
    r = api.post("/commitments/cheques", json={"title": "x", "amount": "1.005", "due_date": "2026-11-15"})
    assert r.status_code == 400 and r.json()["detail"] == "The amount can have at most 2 decimal places in GBP."

    r = api.post("/fixed-assets", json={"name": "Laptop", "category": "it", "cost": "1000.00",
                                        "acquired_on": "2026-01-01", "method": "straight_line", "life_months": 36})
    assert r.status_code == 201, r.text
    rows = api.get(f"/fixed-assets/{r.json()['id']}").json()["schedule"]
    amounts = [Decimal(str(x["amount"])) for x in rows]
    assert sum(amounts) == Decimal("1000") and set(amounts) <= {Decimal("27.78"), Decimal("27.77")}

    a, b, c = (_entity(api, "shareholder", n) for n in ("Ann", "Ben", "Cat"))
    for eid, pct in ((a, 33.3333), (b, 33.3333), (c, 33.3334)):
        assert api.post("/equity/shareholdings", json={"entity_id": eid, "percent": pct}).status_code == 201
    r = api.post("/equity/dividend/declare", json={"total_amount": "1000.01", "date": "2026-10-01"})
    assert r.status_code == 201, r.text
    allocs = [x["amount"] for x in r.json()["allocations"]]
    assert sum(Decimal(str(x)) for x in allocs) == Decimal("1000.01") and sorted(allocs) == [333.33, 333.34, 333.34]


def test_budgets_rules_and_thresholds_keep_pence(uk):
    api, _ = uk
    r = api.post("/budgets", json={"month": "2026-10", "category": "Bank charges", "limit_amount": "99.99"})
    assert r.status_code == 201 and r.json()["limit_amount"] == 99.99, r.text
    r = api.post("/budgets/roll-forward", json={"from_month": "2026-10", "months": 1, "change_pct": 10})
    assert r.status_code == 200, r.text
    got = api.get("/budgets", params={"month": "2026-11"}).json()
    assert got[0]["limit_amount"] == 109.99                                  # 109.989 → 109.99, not 110
    assert api.post("/budgets", json={"month": "2026-12", "category": "x", "limit_amount": "1.001"}).status_code == 400

    r = api.post("/recurring/from-text", json={"text": "pay Acme 12.50 monthly"})
    assert r.status_code == 201 and r.json()["amount"] == 12.5, r.text
    r = api.put("/ai-accountant/guardrails", json={"approval_threshold": "499.99"})
    assert r.status_code == 200 and r.json()["approval_threshold"] == 499.99, r.text
    r = api.post("/expenses/settings", json={"approval_threshold": "250.50"})
    assert r.status_code == 200 and r.json()["approval_threshold"] == 250.5, r.text


def test_a_rial_payroll_rounds_as_it_always_has(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    try:
        emp = _entity(api, "employee", "سارا")
        assert api.post("/payroll/profiles", json={"entity_id": emp, "pay_type": "hourly", "hourly_rate": 333_333,
                                                   "standard_hours": 176, "income_tax_rate": 0.1}).status_code == 201
        run = api.post("/payroll/runs", json={"period_start": "2026-09-01", "period_end": "2026-09-30",
                                              "pay_date": "2026-09-30", "employees": [{"entity_id": emp, "hours": 7.5}]})
        ln = run.json()["lines"][0]
        assert ln["gross"] == 2_499_998 and isinstance(ln["gross"], int)       # 2,499,997.5 half-up, whole rials as before
        assert ln["income_tax"] == 250_000                                      # 249,999.8
        r = api.post("/payroll/profiles", json={"entity_id": emp, "pay_type": "salaried", "base_salary": "15000000.5"})
        assert r.status_code == 400 and r.json()["detail"] == "The amount can't have decimals in IRR."
    finally:
        client.cookies.clear()
        _purge_company(db, cid)


MODULE_PAGES = [
    "/payroll/profiles", "/payroll/runs", "/payroll/year-summary?year=2026", "/payroll/hours-summary",
    "/expenses", "/expenses/settings", "/fixed-assets", "/fixed-assets/depreciation-run", "/commitments",
    "/commitments/summary", "/budgets", "/budgets/actual-vs-budget?month=2026-10", "/recurring", "/equity/cap-table",
    "/petty-cash/accounts", "/manager-reports/inventory/balance", "/manager-reports/inventory/movements",
    "/insights", "/summary", "/reports/owner-dashboard", "/reports/cash-forecast", "/brain/cfo/report",
    "/notifications/digest-settings", "/ai-accountant/guardrails", "/manager-reports/close-pack/checklist",
]


def test_every_module_page_reads_pence(uk):
    """Every module with pence in it: each page answers (an int() cut the
    pence, a Decimal met a float, an int field refused 7.5)."""
    api, _ = uk
    emp = _entity(api, "employee", "Sam Hartley")
    api.post("/payroll/profiles", json={"entity_id": emp, "pay_type": "salaried", "base_salary": "2083.33",
                                        "income_tax_rate": 0.2, "social_security_rate": 0.08})
    run = api.post("/payroll/runs", json={"period_start": "2026-09-01", "period_end": "2026-09-30", "pay_date": "2026-09-30"})
    assert run.status_code == 201, run.text
    api.post(f"/payroll/runs/{run.json()['id']}/post")
    api.post("/expenses/settings", json={"mileage_rate": 0.45, "approval_threshold": "250.50"})
    api.post("/expenses/mileage", json={"employee_name": "Sam Hartley", "claim_date": "2026-10-01", "distance": 123})
    api.post("/fixed-assets", json={"name": "Laptop", "cost": "1000.00", "acquired_on": "2026-01-01",
                                    "method": "straight_line", "life_months": 36})
    api.post("/commitments/installments", json={"title": "Van loan", "total_amount": "100.01", "count": 3,
                                                 "first_due": "2026-11-01"})
    api.post("/budgets", json={"month": "2026-10", "category": "Bank charges", "limit_amount": "99.99"})
    api.post("/recurring", json={"name": "Rent", "amount": "950.50", "start_date": "2026-11-01",
                                 "next_run_date": "2026-11-01", "counter_account_code": "7200"})
    api.put("/ai-accountant/guardrails", json={"approval_threshold": "499.99"})
    failed = {}
    for path in MODULE_PAGES:
        r = api.get(path)
        if r.status_code >= 500:
            failed[path] = r.status_code
    assert failed == {}, failed
