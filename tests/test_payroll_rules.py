"""Statutory payroll rules (roadmap §3.3): the 1405 parameters live in a
table, statutory-mode profiles get progressive tax + capped insurance + the
fixed allowances, the employer's share is posted as a cost, and the monthly
insurance / salary-tax lists export from a run.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.payroll import (
    PayProfileUpsert,
    PayRunCreate,
    create_run,
    insurance_list_csv,
    post_run,
    tax_list_csv,
    upsert_profile,
    void_run,
    year_summary,
)
from app.models.payroll_rules import PayrollRuleSet
from app.services import payroll_service
from app.services.payroll_rules import (
    IR_1405,
    UK_2026,
    RuleParams,
    parse_params,
    rules_in_force,
    seed_payroll_rules,
)
from tests.test_payroll import _employee, _leg, _txn_balanced, ir, uk  # noqa: F401  (fixtures)

IR = RuleParams.model_validate(IR_1405)
UK = RuleParams.model_validate(UK_2026)
MIN_DAILY = 5_541_850


# ─── 1. Pure maths ─────────────────────────────────────────────────────

@pytest.mark.parametrize("taxable,expected", [
    (0, 0),
    (400_000_000, 0),                                   # exempt band edge
    (500_000_000, 10_000_000),                          # 10 % of the 100 M above 400 M
    (900_000_000, 40_000_000 + 15_000_000),             # 400 M @10 % + 100 M @15 %
    (1_500_000_000, 40_000_000 + 30_000_000 + 40_000_000 + 50_000_000 + 30_000_000),
])
def test_progressive_tax_1405_brackets(taxable, expected):
    assert payroll_service.progressive_tax(taxable, IR.tax_brackets) == expected


def test_progressive_tax_accepts_plain_dicts():
    brackets = [{"upto": 100, "rate": 0}, {"upto": None, "rate": 0.5}]
    assert payroll_service.progressive_tax(300, brackets) == 100


def test_statutory_ir_allowances_insurance_and_tax():
    c = payroll_service.calculate(pay_type="salaried", base_salary=200_000_000, rules=IR)
    assert c.allowances == 30_000_000 + 22_000_000          # مسکن + بن
    assert c.gross == 252_000_000
    assert c.insurable_wage == 252_000_000
    assert c.social_security == round(252_000_000 * 0.07)  # 17,640,000 worker's share
    assert c.employer_social == round(252_000_000 * 0.23)  # 57,960,000 employer's share
    assert c.taxable_base == 252_000_000 - c.social_security  # 7 % deducted before tax
    assert c.income_tax == 0                                # under the 400 M exemption
    assert c.net_pay == c.gross - c.social_security
    # The posting identity still holds: gross = tax + social + deductions + net.
    assert c.gross == c.income_tax + c.social_security + c.pre_tax_deductions + c.net_pay


def test_statutory_ir_children_and_seniority():
    c = payroll_service.calculate(
        pay_type="salaried", base_salary=200_000_000, rules=IR, children=2, seniority_eligible=True,
    )
    child = 2 * 3 * MIN_DAILY                      # حق اولاد: 3 days' wage per child
    seniority = 166_667 * 30                        # پایه سنوات per month
    assert c.allowances == 52_000_000 + child + seniority
    assert c.gross == 200_000_000 + c.allowances
    # Child allowance is exempt from insurance; everything else is insurable.
    assert c.insurable_wage == c.gross - child


def test_statutory_ir_insurance_ceiling_and_top_bracket():
    c = payroll_service.calculate(pay_type="salaried", base_salary=2_000_000_000, rules=IR)
    ceiling = 7 * MIN_DAILY * 30
    assert c.insurable_wage == ceiling
    assert c.social_security == round(ceiling * 0.07)
    assert c.employer_social == round(ceiling * 0.23)
    taxable = c.gross - c.social_security
    assert c.income_tax == payroll_service.progressive_tax(taxable, IR.tax_brackets)
    assert c.income_tax > 0.25 * (taxable - 1_400_000_000)  # reaches the 30 % band


def test_statutory_proration_scales_allowances():
    full = payroll_service.calculate(pay_type="salaried", base_salary=300_000_000, rules=IR)
    half = payroll_service.calculate(pay_type="salaried", base_salary=300_000_000, rules=IR, proration=0.5)
    assert half.allowances == round(full.allowances / 2)
    assert half.gross == 150_000_000 + half.allowances


def test_statutory_uk_bands_and_ni():
    c = payroll_service.calculate(pay_type="salaried", base_salary=3000, rules=UK)
    assert c.allowances == 0
    assert c.insurable_wage == 3000
    assert c.social_security == 240                # 8 % employee NI
    assert c.employer_social == 450                # 15 % employer NI
    assert c.taxable_base == 3000                  # NI does not reduce taxable pay
    assert c.income_tax == round((3000 - 1048) * 0.20)
    assert c.net_pay == 3000 - c.income_tax - 240


def test_statutory_uk_below_primary_threshold_pays_no_ni():
    c = payroll_service.calculate(pay_type="salaried", base_salary=900, rules=UK)
    assert c.social_security == 0 and c.employer_social == 0 and c.income_tax == 0
    assert c.net_pay == 900


def test_flat_mode_unchanged_when_no_rules():
    c = payroll_service.calculate(
        pay_type="salaried", base_salary=5000, income_tax_rate=0.2, social_security_rate=0.1, pension_rate=0.05,
    )
    assert (c.gross, c.net_pay, c.allowances, c.employer_social, c.insurable_wage) == (5000, 3300, 0, 0, 0)


# ─── 2. Parameter validation ────────────────────────────────────────────

def test_rule_params_reject_unordered_or_misplaced_brackets():
    with pytest.raises(ValidationError):
        RuleParams.model_validate({"tax_brackets": [{"upto": 500, "rate": 0}, {"upto": 400, "rate": 0.1}]})
    with pytest.raises(ValidationError):
        RuleParams.model_validate({"tax_brackets": [{"upto": None, "rate": 0}, {"upto": 400, "rate": 0.1}]})
    with pytest.raises(ValidationError):
        RuleParams.model_validate({"insurance_employee_rate": 1.5})
    with pytest.raises(ValidationError):
        RuleParams.model_validate({"currency": "RIAL"})


def test_seed_defaults_validate_and_parse_round_trip():
    for spec in (IR_1405, UK_2026):
        p = RuleParams.model_validate(spec)
        assert parse_params(json.dumps(p.model_dump())) == p
    assert parse_params(None) == RuleParams()


# ─── 3. Seeding and "in force" selection ────────────────────────────────

def test_seed_is_idempotent_and_never_overwrites_edits(ir):
    assert seed_payroll_rules(ir) == 2
    assert seed_payroll_rules(ir) == 0
    row = ir.execute(select(PayrollRuleSet).where(PayrollRuleSet.locale == "ir")).scalar_one()
    params = json.loads(row.params)
    params["housing_allowance"] = 99
    row.params = json.dumps(params)
    ir.commit()
    assert seed_payroll_rules(ir) == 0
    assert rules_in_force(ir, "ir", date(2026, 6, 1)).params.housing_allowance == 99


def test_rules_in_force_by_date_and_locale(ir):
    seed_payroll_rules(ir)
    assert rules_in_force(ir, "ir", date(2026, 3, 20)) is None            # Esfand 1404: no set yet
    assert rules_in_force(ir, "ir", date(2026, 3, 21)).rule_set.year == "1405"
    assert rules_in_force(ir, "ir", date(2027, 3, 20)).rule_set.year == "1405"
    assert rules_in_force(ir, "ir", date(2027, 3, 21)) is None
    assert rules_in_force(ir, "default", date(2026, 9, 1)).rule_set.locale == "ir"
    assert rules_in_force(ir, "uk", date(2026, 4, 5)) is None
    assert rules_in_force(ir, "uk", date(2026, 4, 6)).rule_set.year == "2026/27"


def test_latest_starting_window_wins(ir):
    seed_payroll_rules(ir)
    ir.add(PayrollRuleSet(locale="ir", year="1405-rev", name="mid-year revision",
                          effective_from=date(2026, 7, 1), effective_to=None,
                          params=json.dumps(RuleParams.model_validate({**IR_1405, "housing_allowance": 1}).model_dump())))
    ir.commit()
    assert rules_in_force(ir, "ir", date(2026, 6, 30)).params.housing_allowance == 30_000_000
    assert rules_in_force(ir, "ir", date(2026, 7, 1)).params.housing_allowance == 1


# ─── 4. End-to-end run, posting and exports (Iran chart) ────────────────

def _statutory_profile(db, emp, **extra):
    return upsert_profile(PayProfileUpsert(
        entity_id=emp.id, pay_type="salaried", base_salary=200_000_000, tax_mode="statutory", **extra,
    ), db)


def test_statutory_profile_requires_a_rule_set(ir):
    emp = _employee(ir, "بدون قانون")
    with pytest.raises(HTTPException) as exc:
        _statutory_profile(ir, emp)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        upsert_profile(PayProfileUpsert(entity_id=emp.id, tax_mode="weird"), ir)
    assert exc.value.status_code == 422


def test_run_without_covering_rule_set_is_rejected(ir):
    seed_payroll_rules(ir)
    emp = _employee(ir, "پیش از ۱۴۰۵")
    _statutory_profile(ir, emp)
    with pytest.raises(HTTPException) as exc:
        create_run(PayRunCreate(period_start=date(2026, 2, 20), period_end=date(2026, 3, 20),
                                pay_date=date(2026, 3, 20)), ir)
    assert exc.value.status_code == 422
    assert "rule set" in exc.value.detail


def test_statutory_run_posts_employer_share_and_exports_lists(ir):
    seed_payroll_rules(ir)
    emp = _employee(ir, "سارا احمدی")
    emp.national_id = "0012345678"
    emp.code = "E-7"
    ir.commit()
    prof = _statutory_profile(ir, emp, children=1)
    assert prof["tax_mode"] == "statutory" and prof["children"] == 1

    run = create_run(PayRunCreate(
        period_start=date(2026, 6, 22), period_end=date(2026, 7, 22), pay_date=date(2026, 7, 22),
    ), ir)
    line = run["lines"][0]
    expected = payroll_service.calculate(pay_type="salaried", base_salary=200_000_000, rules=IR, children=1)
    assert line["gross"] == expected.gross
    assert line["allowances"] == expected.allowances
    assert line["insurable_wage"] == expected.insurable_wage
    assert line["employer_social"] == expected.employer_social
    assert run["total_employer_social"] == expected.employer_social
    assert run["total_social"] == expected.social_security

    posted = post_run(UUID(run["id"]), ir)
    txn = posted["post_transaction_id"]
    dr, cr = _txn_balanced(ir, txn)
    assert dr == cr == expected.gross + expected.employer_social
    assert _leg(ir, txn, "6110") == (expected.gross, 0)                 # gross wages
    assert _leg(ir, txn, "6111") == (expected.employer_social, 0)       # employer insurance cost
    assert _leg(ir, txn, "2170") == (0, expected.social_security + expected.employer_social)
    assert _leg(ir, txn, "2180") == (0, expected.net_pay)

    ins = insurance_list_csv(UUID(run["id"]), ir)
    assert ins.media_type.startswith("text/csv")
    rows = list(csv.reader(io.StringIO(ins.body.decode("utf-8").lstrip("﻿"))))
    assert rows[0][:4] == ["employee_name", "national_id", "employee_code", "days"]
    assert rows[1][0] == "سارا احمدی" and rows[1][1] == "0012345678" and rows[1][2] == "E-7"
    assert rows[1][3] == "30"
    assert int(rows[1][6]) == expected.insurable_wage
    assert int(rows[1][7]) == expected.social_security
    assert int(rows[1][8]) == expected.employer_social
    assert rows[-1][0] == "TOTAL" and int(rows[-1][8]) == expected.employer_social

    tax = tax_list_csv(UUID(run["id"]), ir)
    trows = list(csv.reader(io.StringIO(tax.body.decode("utf-8").lstrip("﻿"))))
    assert trows[0][0] == "employee_name"
    assert int(trows[1][6]) == expected.taxable_base and int(trows[1][7]) == expected.income_tax
    assert int(trows[-1][2]) == expected.gross

    ys = year_summary(2026, None, ir)
    assert ys["employees"][0]["employer_social"] == expected.employer_social

    # Void reverses the employer legs too.
    voided = void_run(UUID(run["id"]), ir)
    assert voided["status"] == "voided"
    with pytest.raises(HTTPException) as exc:
        insurance_list_csv(UUID(run["id"]), ir)
    assert exc.value.status_code == 404


def test_flat_and_statutory_profiles_mix_in_one_run(ir):
    seed_payroll_rules(ir)
    a = _employee(ir, "Flat")
    b = _employee(ir, "Statutory")
    upsert_profile(PayProfileUpsert(entity_id=a.id, pay_type="salaried", base_salary=100_000_000,
                                    income_tax_rate=0.1), ir)
    _statutory_profile(ir, b)
    run = create_run(PayRunCreate(period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
                                  pay_date=date(2026, 5, 31)), ir)
    by_name = {ln["employee_name"]: ln for ln in run["lines"]}
    assert by_name["Flat"]["income_tax"] == 10_000_000 and by_name["Flat"]["employer_social"] == 0
    assert by_name["Statutory"]["allowances"] == 52_000_000
    assert run["total_employer_social"] == by_name["Statutory"]["employer_social"]


def test_uk_statutory_run_posts_employer_ni(uk):
    seed_payroll_rules(uk)
    emp = _employee(uk, "Tom Reed")
    upsert_profile(PayProfileUpsert(entity_id=emp.id, pay_type="salaried", base_salary=3000,
                                    tax_mode="statutory"), uk)
    run = create_run(PayRunCreate(period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
                                  pay_date=date(2026, 5, 31)), uk)
    assert run["lines"][0]["income_tax"] == round((3000 - 1048) * 0.2)
    assert run["lines"][0]["social_security"] == 240
    posted = post_run(UUID(run["id"]), uk)
    txn = posted["post_transaction_id"]
    assert _leg(uk, txn, "7101") == (450, 0)
    assert _leg(uk, txn, "2212") == (0, 240 + 450)


# ─── 5. HTTP: who may read and who may edit ─────────────────────────────

def _cleanup(db, year):
    for r in db.execute(select(PayrollRuleSet).where(PayrollRuleSet.year == year)).scalars().all():
        db.delete(r)
    db.commit()


def test_rule_sets_read_by_payroll_roles_but_edited_by_superadmin_only(client, db):
    from tests.conftest import _CSRFTestClient
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings

    year = f"t-{uuid.uuid4().hex[:6]}"
    body = {
        "locale": "ir", "year": year, "name": "test set",
        "effective_from": "2030-03-21", "effective_to": "2031-03-20",
        "params": {**IR_1405, "housing_allowance": 42},
    }
    try:
        # Company owner (not super-admin): may read, may not create/edit.
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name,
                           create_session_token(user_id=str(uuid.uuid4()), username="owner1", is_admin=True))
        client.cookies.set(CSRF_COOKIE, csrf)
        owner = _CSRFTestClient(client, csrf)
        assert owner.get("/payroll/rules").status_code == 200
        r = owner.get("/payroll/rules/active", params={"on": "2026-09-01"})
        assert r.status_code == 200 and r.json()["locale"] in ("ir", "uk")
        r = owner.get("/payroll/rules/active", params={"on": "2026-09-01", "locale": "uk"})
        assert r.status_code == 200 and r.json()["locale"] == "uk"
        assert owner.post("/payroll/rules", json=body).status_code == 403

        # Super-admin: create, list, edit; the edit is audited.
        client.cookies.clear()
        csrf = generate_csrf_token()
        client.cookies.set(settings.auth_cookie_name,
                           create_session_token(user_id=str(uuid.uuid4()), username="sa", is_admin=True,
                                                is_superadmin=True))
        client.cookies.set(CSRF_COOKIE, csrf)
        sa = _CSRFTestClient(client, csrf)
        r = sa.post("/payroll/rules", json=body)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["params"]["housing_allowance"] == 42
        assert sa.post("/payroll/rules", json=body).status_code == 409          # same locale+year
        bad = {**body, "params": {**body["params"], "tax_brackets": [{"upto": 5, "rate": 0}, {"upto": 1, "rate": 0.1}]}}
        assert sa.post("/payroll/rules", json={**bad, "year": year + "x"}).status_code == 422

        r = sa.put(f"/payroll/rules/{created['id']}", json={"params": {**body["params"], "housing_allowance": 43}})
        assert r.status_code == 200 and r.json()["params"]["housing_allowance"] == 43
        assert sa.put(f"/payroll/rules/{created['id']}", json={"effective_to": "2029-01-01"}).status_code == 422
        assert sa.put(f"/payroll/rules/{uuid.uuid4()}", json={"name": "x"}).status_code == 404

        listed = sa.get("/payroll/rules").json()
        assert any(x["id"] == created["id"] for x in listed)
        active = sa.get("/payroll/rules/active", params={"on": "2030-06-01", "locale": "ir"}).json()
        assert active["rule_set"] and active["rule_set"]["year"] == year

        from app.models.audit_log import AuditLog
        trail = db.execute(select(AuditLog).where(AuditLog.entity_type == "payroll_rule_set",
                                                  AuditLog.entity_id == created["id"])).scalars().all()
        assert {a.action for a in trail} >= {"create", "update"}
        assert any("housing_allowance" in (a.detail or "") for a in trail)
    finally:
        client.cookies.clear()
        _cleanup(db, year)
        _cleanup(db, year + "x")
