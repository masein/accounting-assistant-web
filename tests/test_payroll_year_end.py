"""Year-end pay: عیدی و پاداش and حق سنوات (roadmap 2026-09 §3.3)."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.payroll import (
    PayProfileUpsert, PayRunCreate, YearEndCreate, create_run, create_year_end_run, get_run, list_profiles, pay_run,
    post_run, upsert_profile,
)
from app.services import payroll_year_end as ye
from app.services.payroll_rules import IR_1405, RuleParams, seed_payroll_rules
from app.services.payroll_service import progressive_tax
from tests.test_payroll import _employee, _leg, _txn_balanced, ir, uk  # noqa: F401  (fixtures)

IR = RuleParams.model_validate(IR_1405)
MIN_DAILY = 5_541_850
CAP = 3 * 30 * MIN_DAILY                                   # three months of the minimum wage: 498,766,500
YEAR = (date(2026, 3, 21), date(2027, 3, 20))              # 1405: 365 days
PAY = date(2027, 3, 10)


def half_up(x) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _profile(db, name, base, **extra):
    emp = _employee(db, name)
    upsert_profile(PayProfileUpsert(entity_id=emp.id, pay_type="salaried", base_salary=base, **extra), db)
    return emp


def _year_end(db, **kw):
    return create_year_end_run(YearEndCreate(**{"year": "1405", "pay_date": PAY, **kw}), db)


# --- the amounts -----------------------------------------------------------------------------------------------

def test_a_full_year_and_a_mid_year_hire(ir):
    seed_payroll_rules(ir)
    _profile(ir, "Ali Full Year", 200_000_000)
    _profile(ir, "Bita Joined Mehr", 500_000_000, hired_on=date(2026, 9, 23))      # 1 Mehr 1405
    run = _year_end(ir)
    assert (run["kind"], run["year_key"], run["status"]) == ("year_end", "1405", "draft")
    assert (run["period_start"], run["period_end"]) == ("2026-03-21", "2027-03-20")
    ali, bita = run["lines"]                                                        # by name
    # a full year: two months' wage (under the cap) and a month's wage of سنوات
    assert (ali["days_worked"], ali["eidi"], ali["sanavat"]) == (365, 400_000_000, 200_000_000)
    # 179 days from 1 Mehr; two months of 500 M is over the cap, so the cap, pro-rated
    assert bita["days_worked"] == 179
    assert bita["eidi"] == half_up(Decimal(CAP) * 179 / 365)
    assert bita["sanavat"] == half_up(Decimal(500_000_000) * 179 / 365)
    for ln in (ali, bita):
        assert ln["gross"] == ln["eidi"] + ln["sanavat"]
        assert ln["income_tax"] == 0                                               # within one month's exemption
        assert ln["social_security"] == ln["employer_social"] == ln["insurable_wage"] == 0   # no insurance
        assert ln["net_pay"] == ln["gross"]
    assert run["total_gross"] == ali["gross"] + bita["gross"] and run["total_social"] == 0


def test_the_eidi_above_one_months_exemption_is_taxed_on_top_of_the_salary(ir):
    seed_payroll_rules(ir)
    emp = _profile(ir, "Cyrus", 750_000_000)
    regular = create_run(PayRunCreate(period_start=date(2026, 6, 22), period_end=date(2026, 7, 22),
                                      pay_date=date(2026, 7, 22)), ir)
    post_run(UUID(regular["id"]), ir)
    run = _year_end(ir)
    (ln,) = run["lines"]
    taxable = CAP - 400_000_000                                                    # 98,766,500 over the exemption
    assert ln["eidi"] == CAP and ln["taxable_base"] == taxable
    # on top of the 750 M month: part in the 10 % band, part in the 15 % band
    assert ln["income_tax"] == progressive_tax(750_000_000 + taxable, IR.tax_brackets) - progressive_tax(750_000_000, IR.tax_brackets)
    assert ln["income_tax"] == 5_000_000 + half_up(Decimal(taxable - 50_000_000) * Decimal("0.15"))
    assert ln["net_pay"] == ln["gross"] - ln["income_tax"]
    assert ln["sanavat"] == 750_000_000                                            # سنوات: no tax
    # with no regular run in the year, the excess is taxed from the first taxed band
    alone = ye.calculate(ir.query(ye.EmployeePayProfile).one(), IR, start=YEAR[0], end=YEAR[1])
    assert alone.income_tax == half_up(Decimal(taxable) * Decimal("0.10"))
    assert emp is not None


def test_days_typed_seniority_and_hourly_wages(ir):
    seed_payroll_rules(ir)
    dina = _profile(ir, "Dina", 100_000_000, seniority_eligible=True)
    emp = _employee(ir, "Hourly Hamid")
    upsert_profile(PayProfileUpsert(entity_id=emp.id, pay_type="hourly", hourly_rate=1_000_000,
                                    monthly_standard_hours=176), ir)
    run = _year_end(ir, employees=[{"entity_id": dina.id, "days_worked": 100}, {"entity_id": emp.id}])
    by = {ln["employee_name"]: ln for ln in run["lines"]}
    wage = 100_000_000 + 166_667 * 30                                              # base + پایه سنوات
    assert by["Dina"]["days_worked"] == 100
    assert by["Dina"]["eidi"] == half_up(Decimal(wage * 2) * 100 / 365)
    assert by["Dina"]["sanavat"] == half_up(Decimal(wage) * 100 / 365)
    assert by["Hourly Hamid"]["eidi"] == 2 * 176_000_000                           # 176 h a month × 1 M
    # someone hired after the year ended gets nothing
    late = ye.calculate(type("P", (), {"pay_type": "salaried", "base_salary": 1, "hired_on": date(2027, 5, 1),
                                        "seniority_eligible": False})(), IR, start=YEAR[0], end=YEAR[1])
    assert (late.days_worked, late.eidi, late.sanavat) == (0, 0, 0)


def test_rule_parameters():
    assert ye.sanavat_days(IR) == 30 and ye.eid_exemption(IR) == 400_000_000
    legacy = RuleParams.model_validate({k: v for k, v in IR_1405.items() if k != "sanavat_days_per_year"})
    assert legacy.sanavat_days_per_year is None and ye.sanavat_days(legacy) == 30   # sets stored before this change
    assert ye.sanavat_days(RuleParams()) == 0                                      # no عیدی, no سنوات
    custom = RuleParams.model_validate({**IR_1405, "sanavat_days_per_year": 0, "eid_exempt_monthly": 0})
    line = ye.calculate(type("P", (), {"pay_type": "salaried", "base_salary": 100_000_000, "hired_on": None,
                                        "seniority_eligible": False})(), custom, start=YEAR[0], end=YEAR[1])
    assert line.sanavat == 0 and line.taxable_eidi == 200_000_000
    assert ye.year_window("1405") == YEAR and ye.current_year_key(date(2026, 3, 20)) == "1404"
    for bad in ("abc", "2026", "1200"):
        with pytest.raises(ye.YearEndError):
            ye.year_window(bad)


# --- the run's life ----------------------------------------------------------------------------------------------

def test_post_pay_and_the_payslip(ir, monkeypatch):
    seed_payroll_rules(ir)
    emp = _profile(ir, "سارا احمدی", 200_000_000)
    run = _year_end(ir)
    posted = post_run(UUID(run["id"]), ir)
    txn = posted["post_transaction_id"]
    dr, cr = _txn_balanced(ir, txn)
    assert dr == cr == 600_000_000
    assert _leg(ir, txn, "6110") == (600_000_000, 0)                               # the year's pay, as wages
    assert _leg(ir, txn, "2180") == (0, 600_000_000)                               # all of it owed: no tax, no insurance
    paid = pay_run(UUID(run["id"]), db=ir)
    assert paid["status"] == "paid" and paid["kind"] == "year_end"

    from app.models.pay_run import PayRun, PayRunLine
    from app.services.documents import render
    seen = {}
    monkeypatch.setattr(render, "render_pdf", lambda ctx, *a, **k: seen.update(ctx) or b"%PDF")
    from app.services.documents.labels import labels_for
    r = ir.get(PayRun, UUID(run["id"]))
    ln = ir.query(PayRunLine).filter_by(run_id=r.id).one()
    brand, _l, _loc = render._ctx(ir)
    for loc, title, labels in (
            ("uk", "YEAR-END PAYSLIP", ["Days worked in the year: 365", "Year-end bonus (Eidi)", "Service pay (Sanavat)"]),
            ("ir", "فیش عیدی و سنوات", ["روزهای کارکرد در سال: ۳۶۵", "عیدی و پاداش", "حق سنوات"])):
        monkeypatch.setattr(render, "_ctx", lambda db, loc=loc: ({**brand, "locale": loc}, labels_for(loc), loc))
        render.render_payslip_pdf(ir, r, ln, emp)
        rows = [[c["value"] for c in row["cells"]] for row in seen["rows"]]
        assert seen["title"] == title and [c[0] for c in rows[:3]] == labels, loc
        assert rows[1][1] and rows[2][1]                                           # with their amounts


def test_what_a_year_end_run_refuses(ir):
    seed_payroll_rules(ir)
    emp = _profile(ir, "Ali", 200_000_000)
    _year_end(ir)
    with pytest.raises(HTTPException) as e:
        _year_end(ir)                                                              # the same year twice
    assert e.value.status_code == 409 and "Already in a year-end run for 1405" in e.value.detail
    # the monthly run for the same days isn't blocked by the year-end one
    create_run(PayRunCreate(period_start=date(2026, 9, 23), period_end=date(2026, 10, 22),
                            pay_date=date(2026, 10, 22)), ir)
    with pytest.raises(HTTPException) as e:
        _year_end(ir, year="1410")                                                 # no rule set
    assert e.value.status_code == 422 and "rule set" in e.value.detail
    with pytest.raises(HTTPException) as e:
        _year_end(ir, year="1405", employees=[{"entity_id": _employee(ir, "No profile").id}])
    assert e.value.status_code == 422 and "No pay profile" in e.value.detail
    with pytest.raises(ValidationError):
        YearEndCreate(year="2026", pay_date=PAY)
    assert get_run(ir.query(ye.PayRun).filter_by(kind="year_end").one().id, ir)["kind"] == "year_end"
    assert emp is not None


def test_a_uk_company_has_no_year_end_pay(uk):
    seed_payroll_rules(uk)
    _profile(uk, "Alice", 3_000)
    with pytest.raises(HTTPException) as e:
        _year_end(uk)
    assert e.value.status_code == 422 and "Iranian" in e.value.detail


def test_the_hire_date_is_kept_on_the_profile(ir):
    emp = _employee(ir, "Nima")
    out = upsert_profile(PayProfileUpsert(entity_id=emp.id, base_salary=1, hired_on=date(2025, 1, 5)), ir)
    assert out["hired_on"] == "2025-01-05"
    assert [p["hired_on"] for p in list_profiles(ir)] == ["2025-01-05"]


def test_who_may_run_year_end_pay():
    from app.core.auth import SessionUser
    from app.core.permissions import user_can_access
    for role, ok in (("owner", True), ("cfo", True), ("accountant", True), ("manager", False), ("employee", False),
                     ("viewer", False), ("personal", False)):
        u = SessionUser(user_id="u", username="u", role=role, is_admin=False)
        assert user_can_access(u, "POST", "/payroll/runs/year-end") is ok, role


def test_the_year_end_panel_is_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    for el in ('id="pr-yearend"', 'id="pr-ye-year"', 'id="pr-ye-paydate"', 'id="pr-ye-btn"', 'id="pr-hired"'):
        assert el in html, el
    assert 'class="ir-only" id="pr-yearend"' in html                                 # Iranian companies only
    js = open("app/static/js/11-time-expenses-payroll.js", encoding="utf-8").read()
    assert "'/payroll/runs/year-end'" in js and "hired_on:" in js and "r.kind === 'year_end'" in js
    text = i18n_text()
    for k in ("payrollHiredOn", "payrollHiredSince", "payrollYearEndTitle", "payrollYearEndYear", "payrollYearEndBtn",
              "payrollYearEndHint", "payrollYearEndRun", "payrollYearEndLine", "payrollYearEndFailed"):
        assert text.count(f"{k}:") == 4, k
