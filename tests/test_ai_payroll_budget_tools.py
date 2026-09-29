"""AI tools for payroll, budgets and month end (roadmap 2026-09 §5.1).
Reads answer from the same data the pages show; proposals go through
execute_proposal and the route code the Payroll and Budgets pages use."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.audit_log import AuditLog
from app.models.budget import BudgetLimit
from app.models.pay_run import PayRun
from app.services.ai_accountant import guardrails
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.execute_service import execute_proposal
from app.services.ai_accountant.payroll_tools import (
    GetPayroll, GetPayrollInput, PayRunRefInput, ProposePayPayRun, ProposePayPayRunInput, ProposePostPayRun,
    ProposeRunPayroll, ProposeRunPayrollInput,
)
from app.services.ai_accountant.period_tools import (
    GetBudgetStatus, GetBudgetStatusInput, GetCloseChecklist, GetCloseChecklistInput, ProposeSetBudget,
    ProposeSetBudgetInput,
)
from tests.test_payroll_lifecycle_http import _balanced, _employee, _run, co  # noqa: F401 — fixture

# Khordad 1405 runs from 22 May to 21 June 2026
KHORDAD = {"period_start": date(2026, 5, 22), "period_end": date(2026, 6, 21)}


def _tool(cid, db, tool, args):
    with use_company(cid):
        return asyncio.run(tool.run(ToolContext(db=db, user_id="u1", username="tester", user_message="test"), args))


def _execute(cid, db, card):
    with use_company(cid):
        res = execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id="u1",
                               actor_username="tester")
        db.commit()
        return res


def _runs(cid, db):
    with use_company(cid):
        db.expire_all()
        return db.execute(select(PayRun).order_by(PayRun.period_start)).scalars().all()


@pytest.fixture()
def staff(co, db):
    from app.db.seed import seed_chart_if_empty
    session, cid = co
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    api = session()
    sara = _employee(api, "Sara Ahmadi")
    reza = _employee(api, "Reza Karimi", base_salary=80_000_000)
    return api, cid, sara, reza


# --- payroll ---------------------------------------------------------------------------------------------------

def test_what_did_we_pay_sara(staff, db):
    api, cid, sara, _ = staff
    run = _run(api).json()
    assert api.post(f"/payroll/runs/{run['id']}/post").status_code == 200
    _run(api, start="2026-06-22", end="2026-07-22", pay="2026-07-22")               # a draft, not in the books
    out = _tool(cid, db, GetPayroll(), GetPayrollInput())
    assert [(r["period_start"], r["status"]) for r in out["runs"]] == [("2026-05-22", "posted")]
    assert [r["period_start"] for r in out["drafts_waiting"]] == ["2026-06-22"]
    assert out["runs"][0]["employees"] == 2 and out["runs"][0]["total_gross"] == 180_000_000

    mine = _tool(cid, db, GetPayroll(), GetPayrollInput(employee="sara"))
    assert mine["employee"] == "Sara Ahmadi" and mine["profile"]["base_salary"] == 100_000_000
    (row,) = mine["history"]
    assert (row["gross"], row["income_tax"], row["social_security"]) == (100_000_000, 10_000_000, 7_000_000)
    assert row["net_pay"] == 100_000_000 - 10_000_000 - 7_000_000
    assert mine["totals_by_currency"]["IRR"]["runs"] == 1
    both = _tool(cid, db, GetPayroll(), GetPayrollInput(employee="Sara Ahmadi", include_drafts=True))
    assert [h["status"] for h in both["history"]] == ["draft", "posted"]                # newest first
    later = _tool(cid, db, GetPayroll(), GetPayrollInput(employee="Sara", from_date=date(2026, 7, 1)))
    assert later["history"] == []
    with pytest.raises(ToolError):
        _tool(cid, db, GetPayroll(), GetPayrollInput(employee="Nobody"))


def test_run_post_and_pay_payroll_from_the_chat(staff, db):
    _api, cid, _sara, _ = staff
    card = _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(**KHORDAD))
    assert card["status"] == "pending" and "2 employee(s): Reza Karimi, Sara Ahmadi" in card["summary"]
    assert "nothing is posted" in card["summary"]
    res = _execute(cid, db, card)
    (run,) = _runs(cid, db)
    assert run.status == "draft" and run.post_transaction_id is None and len(run.lines) == 2
    assert res.transaction_id is None                                                     # a draft posts nothing
    with use_company(cid):
        audit = db.get(AuditLog, uuid.UUID(res.audit_log_id))
        assert (audit.actor_source, audit.entity_type, audit.entity_id) == ("ai-assistant", "pay_run", str(run.id))

    # the same people for the same days again: refused before any card
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(**KHORDAD))
    assert e.value.code == "pay_run_overlap"

    post = _tool(cid, db, ProposePostPayRun(), PayRunRefInput())                        # the latest draft
    assert "gross 180,000,000 IRR" in post["summary"] and post["preview"]["on"] == "2026-06-21"
    res = _execute(cid, db, post)
    (run,) = _runs(cid, db)
    assert run.status == "posted" and res.transaction_id == str(run.post_transaction_id)
    dr, cr = _balanced(db, cid, run.post_transaction_id)
    assert dr == cr > 0
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposePostPayRun(), PayRunRefInput(run="1405-03"))                # by its Jalali month
    assert e.value.code == "pay_run_not_draft"

    pay = _tool(cid, db, ProposePayPayRun(), ProposePayPayRunInput(run="2026-06-01"))   # by a day in the period
    assert pay["preview"]["amount"] == run.total_net and f"{run.total_net:,} IRR" in pay["summary"]
    res = _execute(cid, db, pay)
    (run,) = _runs(cid, db)
    assert run.status == "paid" and res.transaction_id == str(run.pay_transaction_id)
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposePayPayRun(), ProposePayPayRunInput())
    assert e.value.code == "pay_run_not_found"                                            # nothing posted is waiting


def test_payroll_for_named_people_and_what_it_refuses(staff, db):
    api, cid, _sara, reza = staff
    card = _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(**KHORDAD, employees=["Reza"]))
    assert card["preview"]["entity_ids"] == [reza["id"]] and "1 employee(s): Reza Karimi" in card["summary"]
    _execute(cid, db, card)
    (run,) = _runs(cid, db)
    assert [ln.employee_name for ln in run.lines] == ["Reza Karimi"]
    api.post("/entities", json={"type": "employee", "name": "Nima No Profile"})
    for employees, code in ((["Nobody"], "entity_not_found"), (["Nima"], "no_pay_profile")):
        with pytest.raises(ToolError) as e:
            _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(**KHORDAD, employees=employees))
        assert e.value.code == code
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(period_start=date(2026, 6, 1),
                                                                   period_end=date(2026, 5, 1)))
    assert e.value.code == "bad_period"
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposePayPayRun(), ProposePayPayRunInput(run=str(run.id)))
    assert e.value.code == "pay_run_not_posted" and "Post it first" in str(e.value)
    with pytest.raises(ToolError):
        _tool(cid, db, ProposePostPayRun(), PayRunRefInput(run="someday"))


def test_a_year_end_run_doesnt_block_the_monthly_one(staff, db):
    from app.services import payroll_year_end as ye
    from app.services.payroll_rules import seed_payroll_rules
    api, cid, *_ = staff
    with use_company(cid):
        seed_payroll_rules(db)
        ye.create_year_end_run(db, year="1405", pay_date=date(2027, 3, 10))
        db.commit()
    card = _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(**KHORDAD))      # عیدی و سنوات span the year
    assert card["status"] == "pending"
    out = _tool(cid, db, GetPayroll(), GetPayrollInput(include_drafts=True))
    assert [(r["kind"], r["year_key"]) for r in out["drafts_waiting"]] == [("year_end", "1405")]


def test_payroll_proposals_meet_the_guardrails(staff, db):
    api, cid, _sara, _ = staff
    run = _run(api).json()
    draft = _tool(cid, db, ProposeRunPayroll(), ProposeRunPayrollInput(period_start=date(2026, 6, 22),
                                                                       period_end=date(2026, 7, 22)))
    post = _tool(cid, db, ProposePostPayRun(), PayRunRefInput(run=run["id"]))
    with use_company(cid):
        assert guardrails.proposal_amount(db, "propose_run_payroll", draft["preview"]) is None      # moves no money
        assert guardrails.proposal_amount(db, "propose_post_pay_run", post["preview"]) == 180_000_000
        assert guardrails.posting_date("propose_post_pay_run", post["preview"]) == date(2026, 6, 21)
        from app.services.period_service import set_closed_period
        set_closed_period(db, date(2026, 6, 30))
        db.commit()
        with pytest.raises(guardrails.ProposalRefused):
            guardrails.review(db, post)                                                   # posting into a closed month
        assert guardrails.review(db, draft) is not None                                   # a draft is fine


# --- budgets ---------------------------------------------------------------------------------------------------

def _rent_name(cid, db):
    from app.models.account import Account
    with use_company(cid):
        return db.execute(select(Account.name).where(Account.code == "6112")).scalar_one()


def test_set_a_budget_for_three_months_and_read_it_back(staff, db):
    api, cid, *_ = staff
    rent = _rent_name(cid, db)
    with use_company(cid):
        db.add(BudgetLimit(month="1405-08", category=rent, limit_amount=5_000_000))
        db.commit()
    card = _tool(cid, db, ProposeSetBudget(), ProposeSetBudgetInput(category="6112", amount=20_000_000,
                                                                     month="1405-07", months=3))
    assert card["preview"]["months"] == ["1405-07", "1405-08", "1405-09"]
    assert [c["before"] for c in card["preview"]["changes"]] == [None, 5_000_000, None]
    assert "Replaces 1 existing limit(s)." in card["summary"]
    with use_company(cid):
        assert guardrails.proposal_amount(db, "propose_set_budget", card["preview"]) is None       # not money moved
    _execute(cid, db, card)
    with use_company(cid):
        db.expire_all()
        rows = db.execute(select(BudgetLimit.month, BudgetLimit.limit_amount).where(BudgetLimit.category == rent)
                          .order_by(BudgetLimit.month)).all()
    assert rows == [("1405-07", 20_000_000), ("1405-08", 20_000_000), ("1405-09", 20_000_000)]

    # 18 million of rent in Mehr (23 September – 22 October 2026): near the limit
    r = api.post("/transactions", json={"date": "2026-09-25", "description": "rent", "currency": "IRR", "lines": [
        {"account_code": "6112", "debit": 18_000_000, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": 18_000_000}]})
    assert r.status_code == 201, r.text
    out = _tool(cid, db, GetBudgetStatus(), GetBudgetStatusInput(month="1405-07"))
    (b,) = out["budgets"]
    assert (b["category"], b["budget"], b["actual"], b["left"], b["state"]) == (rent, 20_000_000, 18_000_000, 2_000_000, "near")
    assert out["from_date"] == "2026-09-23" and out["over"] == []


def test_budget_categories_are_expense_accounts(staff, db):
    api, cid, *_ = staff
    for text, code in (("1110", "category_not_found"), ("zzz-nothing", "category_not_found")):
        with pytest.raises(ToolError) as e:
            _tool(cid, db, ProposeSetBudget(), ProposeSetBudgetInput(category=text, amount=1))
        assert e.value.code == code
    with pytest.raises(ToolError) as e:
        _tool(cid, db, ProposeSetBudget(), ProposeSetBudgetInput(category="هزینه", amount=1))       # too many
    assert e.value.code == "category_ambiguous"
    with pytest.raises(ToolError):
        _tool(cid, db, ProposeSetBudget(), ProposeSetBudgetInput(category="6112", amount=1, month="1405-13"))
    # no budgets yet: the month's biggest expenses instead
    api.post("/transactions", json={"date": "2026-09-25", "description": "rent", "currency": "IRR", "lines": [
        {"account_code": "6112", "debit": 7_000_000, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": 7_000_000}]})
    out = _tool(cid, db, GetBudgetStatus(), GetBudgetStatusInput(month="1405-07"))
    assert out["budgets"] == [] and out["top_expenses"] == [{"category": _rent_name(cid, db), "actual": 7_000_000}]


# --- month end -------------------------------------------------------------------------------------------------

def test_the_close_checklist_from_the_chat(staff, db):
    api, cid, *_ = staff
    run = _run(api).json()                                                             # a draft for Khordad
    out = _tool(cid, db, GetCloseChecklist(), GetCloseChecklistInput(month="1405-03"))
    assert (out["month"], out["from_date"], out["to_date"]) == ("1405-03", "2026-05-22", "2026-06-21")
    states = {i["key"]: i["state"] for i in out["items"]}
    assert states["payroll"] == "warn" and out["open"] >= 1 and "Monthly close pack" in out["pack"]
    assert next(i for i in out["items"] if i["key"] == "payroll")["item"] == "ثبت حقوق"     # an Iranian company
    api.post(f"/payroll/runs/{run['id']}/post")
    out = _tool(cid, db, GetCloseChecklist(), GetCloseChecklistInput(month="1405-03"))
    assert {i["key"]: i["state"] for i in out["items"]}["payroll"] == "ok"
    with pytest.raises(ToolError):
        _tool(cid, db, GetCloseChecklist(), GetCloseChecklistInput(month="1405-13"))


def test_the_tools_are_in_the_business_registry_only():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT, build_default_registry, build_personal_registry
    names = {"get_payroll", "propose_run_payroll", "propose_post_pay_run", "propose_pay_pay_run",
             "get_budget_status", "propose_set_budget", "get_close_checklist"}
    business = {t.name for t in build_default_registry()}
    personal = {t.name for t in build_personal_registry()}
    assert names <= business and not (names & personal)
    for n in names:
        assert f"``{n}``" in SYSTEM_PROMPT, n
