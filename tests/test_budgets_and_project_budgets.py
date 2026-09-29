"""Budgets you can edit and roll forward, and budgets per project (roadmap §4.7)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.audit_log import AuditLog
from app.models.budget import BudgetLimit
from tests.test_time_billing_and_fees_http import _ent, _log, co, setup  # noqa: F401 — fixtures


def _budget(api, month, category, amount):
    r = api.post("/budgets", json={"month": month, "category": category, "limit_amount": amount})
    assert r.status_code == 201, r.text
    return r.json()


def _limits(db, cid, month):
    with use_company(cid):
        return {b.category: b.limit_amount for b in
                db.execute(select(BudgetLimit).where(BudgetLimit.month == month)).scalars()}


# --- editing a budget --------------------------------------------------------------------------------------------

def test_a_budget_can_be_changed_and_the_change_is_audited(co, db):
    api, cid = co
    rent = _budget(api, "2026-09", "Rent", 5_000_000)
    _budget(api, "2026-09", "Food", 900_000)
    r = api.patch(f"/budgets/{rent['id']}", json={"limit_amount": 6_000_000})
    assert r.status_code == 200 and r.json()["limit_amount"] == 6_000_000
    r = api.patch(f"/budgets/{rent['id']}", json={"category": "Office rent", "month": "2026-10"})
    assert (r.json()["category"], r.json()["month"], r.json()["limit_amount"]) == ("Office rent", "2026-10", 6_000_000)
    # the row in the dashboard table carries its id, so it can be edited from there
    _budget(api, "2026-10", "Food", 700_000)
    rows = api.get("/budgets/actual-vs-budget", params={"month": "2026-10"}).json()["rows"]
    assert {r["category"]: r["id"] for r in rows}["Office rent"] == rent["id"]
    # one budget per category per month
    food_sep = next(b for b in api.get("/budgets", params={"month": "2026-09"}).json() if b["category"] == "Food")
    clash = api.patch(f"/budgets/{food_sep['id']}", json={"month": "2026-10"})
    assert clash.status_code == 409 and "Food" in clash.json()["detail"]
    assert api.patch(f"/budgets/{food_sep['id']}", json={"limit_amount": 0}).status_code == 422
    assert api.patch(f"/budgets/{uuid.uuid4()}", json={"limit_amount": 1}).status_code == 404
    with use_company(cid):
        trail = db.execute(select(AuditLog).where(AuditLog.entity_type == "budget", AuditLog.action == "update")).scalars().all()
    assert len(trail) == 2


# --- rolling budgets forward ------------------------------------------------------------------------------------

def test_roll_forward_copies_a_month_changed_by_a_percentage(co, db):
    api, cid = co
    _budget(api, "2026-09", "Rent", 5_000_000)
    _budget(api, "2026-09", "Food", 333_333)
    _budget(api, "2026-10", "Rent", 7_000_000)                 # October already has rent
    r = api.post("/budgets/roll-forward", json={"from_month": "2026-09", "change_pct": 10})
    assert r.status_code == 200, r.text
    assert r.json() | {} == {"from_month": "2026-09", "months": ["2026-10"], "created": 1, "updated": 0, "kept": 1}
    assert _limits(db, cid, "2026-10") == {"Rent": 7_000_000, "Food": 366_666}   # 366,666.3 → half-up, kept rent
    # overwrite, several months, not compounded
    r = api.post("/budgets/roll-forward", json={"from_month": "2026-09", "months": 3, "change_pct": 10, "overwrite": True})
    assert r.json()["months"] == ["2026-10", "2026-11", "2026-12"]
    for m in ("2026-10", "2026-11", "2026-12"):
        assert _limits(db, cid, m) == {"Rent": 5_500_000, "Food": 366_666}, m
    # the year turns over; a cut rounds half-up and never reaches zero
    _budget(api, "2026-12", "Tiny", 1)
    api.post("/budgets/roll-forward", json={"from_month": "2026-12", "change_pct": -99})
    assert _limits(db, cid, "2027-01")["Tiny"] == 1 and _limits(db, cid, "2027-01")["Rent"] == 55_000
    with use_company(cid):
        assert db.execute(select(AuditLog).where(AuditLog.action == "roll_forward")).scalars().first() is not None


def test_roll_forward_follows_the_jalali_months(co, db):
    api, cid = co
    _budget(api, "1405-12", "اجاره", 10_000_000)             # Esfand 1405
    r = api.post("/budgets/roll-forward", json={"from_month": "1405-12", "months": 2})
    assert r.json()["months"] == ["1406-01", "1406-02"]       # Farvardin, Ordibehesht 1406
    assert _limits(db, cid, "1406-01") == {"اجاره": 10_000_000}


def test_roll_forward_refuses_what_it_cannot_do(co):
    api, _cid = co
    assert api.post("/budgets/roll-forward", json={"from_month": "2031-01"}).status_code == 404   # nothing to copy
    _budget(api, "2026-09", "Rent", 1_000)
    for body in ({"from_month": "2026-09", "months": 13}, {"from_month": "2026-09", "change_pct": -100},
                 {"from_month": "2026-09", "change_pct": 1001}, {"from_month": "Sept"}):
        assert api.post("/budgets/roll-forward", json=body).status_code == 422, body


# --- project budgets ---------------------------------------------------------------------------------------------

def test_a_project_budget_counts_hours_and_fees(setup):
    api, c, dev, designer, proj = setup["api"], setup["client"], setup["dev"], setup["designer"], setup["project"]
    r = api.patch(f"/time/projects/{proj['id']}", json={"budget_hours": 10, "budget_amount": 3_000_000})
    assert r.status_code == 200 and (r.json()["budget_hours"], r.json()["budget_amount"]) == (10.0, 3_000_000)
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 400_000})
    _log(api, dev, hours=2.5, project=proj)                                   # 1,000,000 at today's rate
    _log(api, dev, hours=1, project=proj, billable=False)                     # hours, no fees
    _log(api, dev, hours=3, project=proj, entry_type="leave")                 # not project work
    wo = _log(api, dev, hours=0.5, project=proj)
    assert api.post(f"/time/entries/{wo['id']}/write-off").status_code == 200  # hours, no fees
    rows = {p["id"]: p for p in api.get("/time/project-budgets").json()}
    u = rows[proj["id"]]
    assert (u["hours_used"], u["amount_used"], u["unpriced_hours"]) == (4.0, 1_000_000, 0.0)
    assert (u["hours_pct"], u["amount_pct"], u["status"]) == (40.0, 33.3, "ok")
    # invoiced time counts at the rate billed, even after the rate changes
    assert api.post("/time/invoice", json={"client_id": c["id"], "invoice_date": "2026-06-30"}).status_code == 201
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 900_000})
    u = {p["id"]: p for p in api.get("/time/project-budgets").json()}[proj["id"]]
    assert u["amount_used"] == 1_000_000 and u["hours_billed"] == 2.5
    # a worker with no rate: hours, but fees can't be priced
    _log(api, designer, hours=2, project=proj, when="2026-07-01")
    u = {p["id"]: p for p in api.get("/time/project-budgets").json()}[proj["id"]]
    assert (u["hours_used"], u["unpriced_hours"], u["amount_used"]) == (6.0, 2.0, 1_000_000)
    # more work tips it over the hours budget (and prices at today's rate)
    _log(api, dev, hours=5, project=proj, when="2026-07-02")
    u = {p["id"]: p for p in api.get("/time/project-budgets").json()}[proj["id"]]
    assert u["hours_used"] == 11.0 and u["status"] == "over" and u["amount_used"] == 1_000_000 + 5 * 900_000
    # null clears a budget; a closed project leaves the list
    assert api.patch(f"/time/projects/{proj['id']}", json={"budget_hours": None, "budget_amount": None}).json()["status"] == "none"
    assert api.patch(f"/time/projects/{proj['id']}", json={"status": "closed"}).status_code == 200
    assert proj["id"] not in {p["id"] for p in api.get("/time/project-budgets").json()}
    assert proj["id"] in {p["id"] for p in api.get("/time/project-budgets", params={"include_closed": True}).json()}
    assert api.patch(f"/time/projects/{uuid.uuid4()}", json={"budget_hours": 1}).status_code == 404
    assert api.patch(f"/time/projects/{proj['id']}", json={"budget_hours": -1}).status_code == 422


def test_budgets_in_another_currency_are_left_unpriced(setup):
    api, c, dev = setup["api"], setup["client"], setup["dev"]
    usd = api.post("/time/projects", json={"client_id": c["id"], "name": "US job", "default_currency": "USD"}).json()
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 400_000})     # an IRR rate
    _log(api, dev, hours=2, project=usd)
    u = {p["id"]: p for p in api.get("/time/project-budgets").json()}[usd["id"]]
    assert (u["currency"], u["amount_used"], u["unpriced_hours"], u["hours_used"]) == ("USD", 0, 2.0, 2.0)


def test_project_budgets_cost_a_fixed_number_of_queries(setup):
    from sqlalchemy import event

    from tests.conftest import _engine
    api, c, dev, proj = setup["api"], setup["client"], setup["dev"], setup["project"]
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 100_000})

    def count():
        n = {"q": 0}
        fn = lambda *a, **k: n.__setitem__("q", n["q"] + 1)  # noqa: E731
        event.listen(_engine, "before_cursor_execute", fn)
        try:
            api.get("/time/project-budgets")
        finally:
            event.remove(_engine, "before_cursor_execute", fn)
        return n["q"]
    for i in range(3):
        _log(api, dev, hours=1, project=proj, when=f"2026-06-{10 + i}")
    few = count()
    for i in range(12):
        _log(api, dev, hours=1, project=proj, when=f"2026-07-{10 + i}")
    assert count() <= few                                            # not one query per entry


def test_a_project_near_or_over_its_budget_rings_the_bell(setup, db):
    from datetime import date

    from app.models.notification import Notification
    from app.services.notification_service import refresh_notifications
    api, cid, dev, proj = setup["api"], setup["cid"], setup["dev"], setup["project"]
    api.patch(f"/time/projects/{proj['id']}", json={"budget_hours": 4})
    _log(api, dev, hours=3.5, project=proj)                             # 87.5 %
    with use_company(cid):
        refresh_notifications(db, today=date(2026, 6, 20))
        n = db.execute(select(Notification).where(Notification.dedupe_key == f"project-budget-{proj['id']}")).scalars().one()
    assert (n.level, n.link_page) == ("warning", "time") and "3.5 of 4 hours" in n.message
    _log(api, dev, hours=1, project=proj, when="2026-06-11")            # 112.5 %
    with use_company(cid):
        refresh_notifications(db, today=date(2026, 6, 20))
        db.expire_all()
        n = db.execute(select(Notification).where(Notification.dedupe_key == f"project-budget-{proj['id']}")).scalars().one()
    assert n.level == "high" and n.title.startswith("Project over budget")


def test_only_books_people_see_project_budgets(setup, client):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    api, cid, proj = setup["api"], setup["cid"], setup["project"]
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="emp", is_admin=False, role="employee", company_id=cid))
    client.cookies.set(CSRF_COOKIE, generate_csrf_token())
    assert client.get("/time/projects").status_code == 200              # the picker
    assert client.get("/time/project-budgets").status_code == 403       # the money
    assert client.patch(f"/time/projects/{proj['id']}", json={"budget_hours": 1}).status_code == 403


# --- the UI ------------------------------------------------------------------------------------------------------

def test_the_budget_and_project_panels_are_wired():
    from tests.i18n_source import i18n_text
    html = open("app/static/index.html", encoding="utf-8").read()
    for el in ('id="budget-roll"', 'id="tm-budgets-section"', 'id="tm-budgets-body"'):
        assert el in html, el
    vouchers = open("app/static/js/06-vouchers.js", encoding="utf-8").read()
    assert "budget-edit" in vouchers and "budget-del" in vouchers and "method: 'PATCH'" in vouchers
    forms = open("app/static/js/08-entities-invoices.js", encoding="utf-8").read()
    assert "'/budgets/roll-forward'" in forms
    time_js = open("app/static/js/11-time-expenses-payroll.js", encoding="utf-8").read()
    tab = time_js[time_js.index("async function loadTimeTab()"):time_js.index("async function tmLoadBudgets()")]
    assert "tmLoadBudgets();" in tab
    fn = time_js[time_js.index("async function tmLoadBudgets()"):]
    assert fn.index("if (currentRole === 'employee')") < fn.index("fetch(API + '/time/project-budgets')")  # no 403 for them
    text = i18n_text()
    for k in ("budgetRollBtn", "budgetRollPrompt", "budgetRollBad", "budgetRolled", "budgetEditPrompt", "budgetEditBad",
              "budgetSaveFailed", "budgetDeleteConfirm", "timeBudgetsTitle", "timeBudgetsHint", "timeBudgetHours",
              "timeBudgetFees", "timeBudgetsNone", "timeBudgetNoLimit", "timeBudgetUnpriced", "timeBudgetSet",
              "timeBudgetHoursPrompt", "timeBudgetFeesPrompt", "timeBudgetSaved"):
        assert text.count(f"{k}:") == 4, k
