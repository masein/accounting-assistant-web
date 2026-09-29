"""Employees and managers on their own forms (follow-up to #187): the Time and
Expenses pages used to ask /entities — books-only — so an employee saw "no
workers, no clients" and couldn't log time, and a manager's landing page
collected 403s. And a self-service mileage claim could name anyone."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.mileage_claim import MileageClaim
from tests.test_payroll_lifecycle_http import co  # noqa: F401 — fixture


@pytest.fixture()
def people(co):
    session, cid = co
    owner = session()
    sara = owner.post("/entities", json={"type": "employee", "name": "Sara Ahmadi"}).json()
    reza = owner.post("/entities", json={"type": "employee", "name": "Reza Karimi"}).json()
    contractor = owner.post("/entities", json={"type": "supplier", "name": "Nina Design"}).json()
    aria = owner.post("/entities", json={"type": "client", "name": "Aria Trading"}).json()
    r = owner.post("/expenses/settings", json={"mileage_rate": 5_000, "mileage_unit": "km", "approval_threshold": 0})
    assert r.status_code == 200, r.text
    return {"session": session, "cid": cid, "sara": sara, "reza": reza, "contractor": contractor, "aria": aria}


def test_the_time_form_for_books_people_and_for_an_employee(people):
    p = people
    owner = p["session"]()
    pk = owner.get("/time/pickers").json()
    assert pk["books"] is True and pk["restricted"] is False
    assert {w["name"] for w in pk["workers"]} == {"Sara Ahmadi", "Reza Karimi", "Nina Design"}
    assert [c["name"] for c in pk["clients"]] == ["Aria Trading"]

    emp = p["session"]("employee", entity_id=p["sara"]["id"])
    assert emp.get("/entities?type=employee").status_code == 403                    # still books-only
    pk = emp.get("/time/pickers").json()
    assert pk["restricted"] is True and pk["books"] is False and pk["self"] == p["sara"]["id"]
    assert [w["name"] for w in pk["workers"]] == ["Sara Ahmadi"]                    # only herself
    assert pk["clients"] == [{"id": p["aria"]["id"], "name": "Aria Trading"}]       # names, to bill against
    # and she can log billable time for that client — but not for Reza
    r = emp.post("/time/entries", json={"employee_id": p["sara"]["id"], "client_id": p["aria"]["id"],
                                        "work_date": "2026-09-01", "hours": 3, "billable": True})
    assert r.status_code == 201, r.text
    assert emp.post("/time/entries", json={"employee_id": p["reza"]["id"], "work_date": "2026-09-01",
                                           "hours": 1}).status_code == 403
    unlinked = p["session"]("employee")
    assert unlinked.get("/time/pickers").json()["workers"] == []


def test_the_expenses_page_for_each_role(people):
    p = people
    owner = p["session"]().get("/expenses/pickers").json()
    assert (owner["can_claim"], owner["can_edit_settings"], owner["restricted"]) == (True, True, False)
    assert {e["name"] for e in owner["employees"]} == {"Sara Ahmadi", "Reza Karimi"}
    assert owner["settings"]["mileage_rate"] == 5_000

    emp = p["session"]("employee", entity_id=p["sara"]["id"]).get("/expenses/pickers").json()
    assert (emp["can_claim"], emp["can_edit_settings"], emp["restricted"]) == (True, False, True)
    assert [e["name"] for e in emp["employees"]] == ["Sara Ahmadi"] and emp["settings"]["mileage_unit"] == "km"

    mgr = p["session"]("manager").get("/expenses/pickers").json()                  # approves, doesn't claim
    assert (mgr["can_claim"], mgr["can_edit_settings"]) == (False, False)
    acct = p["session"]("accountant").get("/expenses/pickers").json()
    assert acct["restricted"] is False and len(acct["employees"]) == 2


def test_an_employee_claims_only_their_own_mileage(people, db):
    p = people
    emp = p["session"]("employee", entity_id=p["sara"]["id"])
    r = emp.post("/expenses/mileage", json={"entity_id": p["reza"]["id"], "claim_date": "2026-09-01", "distance": 10})
    assert r.status_code == 403 and "own mileage" in r.json()["detail"]
    r = emp.post("/expenses/mileage", json={"employee_name": "Someone Else", "claim_date": "2026-09-01",
                                            "distance": 10})
    assert r.status_code == 201, r.text                                            # no entity given → her own
    assert (r.json()["entity_id"], r.json()["employee_name"]) == (p["sara"]["id"], "Sara Ahmadi")
    r = emp.post("/expenses/mileage", json={"entity_id": p["sara"]["id"], "claim_date": "2026-09-02", "distance": 4})
    assert r.status_code == 201 and r.json()["amount"] == 20_000
    unlinked = p["session"]("employee")
    r = unlinked.post("/expenses/mileage", json={"employee_name": "Me", "claim_date": "2026-09-01", "distance": 1})
    assert r.status_code == 403 and "linked" in r.json()["detail"]
    with use_company(p["cid"]):
        claims = db.execute(select(MileageClaim)).scalars().all()
    assert {str(c.entity_id) for c in claims} == {p["sara"]["id"]}                 # nothing for Reza or "Me"
    # the owner still claims for anyone
    owner = p["session"]()
    r = owner.post("/expenses/mileage", json={"entity_id": p["reza"]["id"], "claim_date": "2026-09-01", "distance": 1})
    assert r.status_code == 201 and r.json()["entity_id"] == p["reza"]["id"]


def test_who_may_use_the_pickers():
    from app.core.auth import SessionUser
    from app.core.permissions import user_can_access
    for role, time_ok, exp_ok in (("owner", True, True), ("accountant", True, True), ("employee", True, True),
                                  ("manager", False, True), ("viewer", False, False), ("personal", True, True)):
        u = SessionUser(user_id="u", username="u", role=role, is_admin=False)
        assert user_can_access(u, "GET", "/time/pickers") is time_ok, role
        assert user_can_access(u, "GET", "/expenses/pickers") is exp_ok, role


def test_the_forms_ask_the_pickers_not_the_entity_list():
    from tests.i18n_source import i18n_text
    js = open("app/static/js/11-time-expenses-payroll.js", encoding="utf-8").read()
    time_tab = js[js.index("async function loadTimeTab()"):js.index("// Projects and their budgets")]
    assert "'/time/pickers'" in time_tab and "/entities" not in time_tab
    assert "if (pk.books) loadPendingTime();" in time_tab                         # the pushed-entries inbox: books only
    assert "'tm-new-project-btn', 'tm-set-rate-btn'" in time_tab                  # books set-up, hidden otherwise
    exp = js[js.index("async function loadExpenses()"):js.index("updateMileageCalc();", js.index("async function loadExpenses()"))]
    assert "'/expenses/pickers'" in exp and "/entities" not in exp and "/expenses/settings'" not in exp
    html = open("app/static/index.html", encoding="utf-8").read()
    assert 'id="exp-settings-section"' in html and 'id="exp-claim-section"' in html
    assert i18n_text().count("timeNotLinked:") == 4
