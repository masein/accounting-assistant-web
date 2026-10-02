"""A recurring rule can be paid from a bank account opened in the chart, not
only from a bank party's own account (deep browser test, 2026-10-02, #23)."""
from __future__ import annotations

import uuid

from tests_e2e.conftest import wait_until

POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""


def test_the_recurring_form_offers_the_charts_bank_accounts(flow_page):
    page, watch = flow_page("e2e_banks")
    choices = page.evaluate("async () => (await (await fetch('/brain/bank-accounts')).json()).accounts")
    parent = next(c["code"] for c in choices if not c.get("bank"))          # the chart's own bank account
    name = f"Current account {uuid.uuid4().hex[:4]}"
    status, acc = page.evaluate(POST, ["/accounts", {"name": name, "parent_code": parent}])
    assert status == 201, acc
    page.evaluate("() => { location.hash = 'recurring'; }")
    page.wait_for_load_state("networkidle")
    wait_until(page, "(code) => [...document.querySelectorAll('#rec-bank option')].some(o => o.value === code)", acc["code"])
    label = page.evaluate("(code) => [...document.querySelectorAll('#rec-bank option')].find(o => o.value === code).textContent",
                          acc["code"])
    assert name in label and acc["code"] in label, label
    assert watch.problems() == [], watch.problems()
