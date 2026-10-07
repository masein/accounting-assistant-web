"""The pay profile form (scenarios M7 and M8). Picking an employee fills the
form with their profile, so a save changes what was edited and nothing else:
a blank form was saved over the profile, the salary going to 0 and the
statutory rules to flat rates at 0%. A new profile starts on the statutory
rules when a rule set is in force for the company's locale."""
from __future__ import annotations

import os
import uuid

from tests_e2e.conftest import ARTIFACTS, wait_until

POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""
GET = r"""async (path) => { const r = await fetch(path); return [r.status, await r.json()]; }"""


def _employee(page, name):
    status, ent = page.evaluate(POST, ["/entities", {"type": "employee", "name": name}])
    assert status in (200, 201), ent
    return ent


def test_picking_an_employee_loads_their_profile_and_a_save_keeps_it(flow_page):
    page, watch = flow_page("e2e_payroll")
    try:
        run = uuid.uuid4().hex[:4]
        ali, neda = _employee(page, f"Ali {run}"), _employee(page, f"Neda {run}")
        status, prof = page.evaluate(POST, ["/payroll/profiles", {
            "entity_id": ali["id"], "pay_type": "salaried", "base_salary": 3200, "tax_mode": "statutory",
            "children": 2, "seniority_eligible": True, "hired_on": "2025-04-04"}])
        assert status == 201, prof
        _, active = page.evaluate(GET, "/payroll/rules/active")
        assert active["rule_set"], "the e2e company should have a rule set in force (seeded at startup)"

        page.locator('.nav-btn[data-page="payroll"]').first.click()
        page.wait_for_load_state("networkidle")
        wait_until(page, "(id) => [...document.querySelectorAll('#pr-emp option')].some(o => o.value === id)", ali["id"])

        # Ali's profile fills the form
        page.select_option("#pr-emp", ali["id"])
        assert page.input_value("#pr-base") == "3200"
        assert page.input_value("#pr-taxmode") == "statutory"
        assert page.input_value("#pr-children") == "2"
        assert page.is_checked("#pr-seniority")
        assert page.input_value("#pr-hired") == "2025-04-04"
        assert page.locator("#pr-children-wrap").is_visible()        # the statutory fields show

        # one change, one save: everything else stands
        page.fill("#pr-children", "3")
        page.click("#pr-save-profile")
        saved = None
        for _ in range(50):                                          # the save is async: poll the API
            _, rows = page.evaluate(GET, "/payroll/profiles")
            saved = next((p for p in rows if p["entity_id"] == ali["id"]), None)
            if saved and saved["children"] == 3:
                break
            page.wait_for_timeout(200)
        assert saved and saved["children"] == 3, saved
        assert (saved["base_salary"], saved["tax_mode"], saved["seniority_eligible"], saved["hired_on"]) == \
            (3200, "statutory", True, "2025-04-04")

        # an employee without a profile: the defaults, on the rules in force
        page.select_option("#pr-emp", neda["id"])
        assert page.input_value("#pr-base") == "0"
        assert page.input_value("#pr-taxmode") == "statutory"
        assert page.input_value("#pr-children") == "0"
        assert not page.is_checked("#pr-seniority")
        assert page.input_value("#pr-hired") == ""
        assert page.locator("#pr-tax").is_disabled()                 # flat rates don't apply

        # back to Ali: his saved profile again
        page.select_option("#pr-emp", ali["id"])
        assert page.input_value("#pr-children") == "3"
        assert watch.problems() == [], watch.problems()
    except AssertionError:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "payroll-profile-form.png"), full_page=True)
        raise
