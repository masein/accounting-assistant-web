"""Parties from the chart's own accounts (scenarios X1–X3): a debtor sub-account with
entries is offered on اشخاص, ticked and made a party, which then shows its account's
entries. Its own ledger and sub-account, so the shared company's chart is untouched."""
from __future__ import annotations

import os
import uuid

from tests_e2e.conftest import ARTIFACTS, BASE_URL, wait_until


def test_a_debtor_account_becomes_a_party_with_its_entries(flow_page):
    page, watch = flow_page("e2e_parties")
    page.evaluate("() => { location.hash = 'entities'; }")
    page.wait_for_load_state("networkidle")
    csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "aa_csrf")
    api = lambda m, p, body=None: page.request.fetch(f"{BASE_URL}{p}", method=m, data=body,  # noqa: E731
                                                     headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"})
    tag = uuid.uuid4().hex[:5]
    ledger = api("POST", "/accounts", {"name": f"Debtors {tag}", "parent_code": "11"})
    assert ledger.status == 201, ledger.text()
    school = api("POST", "/accounts", {"name": f"Sister school {tag}", "parent_code": ledger.json()["code"]})
    assert school.status == 201, school.text()
    code = school.json()["code"]
    cash = [a["code"] for a in api("GET", "/accounts").json() if a["code"].startswith("111") and len(a["code"]) == 4][0]
    r = api("POST", "/transactions", {"date": "2026-09-01", "description": f"rent share {tag}", "reference": f"X-{tag}",
                                       "lines": [{"account_code": code, "debit": 600000, "credit": 0},
                                                 {"account_code": cash, "debit": 0, "credit": 600000}]})
    assert r.status == 201, r.text()

    page.reload()
    page.evaluate("() => { location.hash = 'entities'; }")
    page.wait_for_load_state("networkidle")
    wait_until(page, "() => !document.getElementById('party-accounts').hidden")
    page.click("#party-accounts > summary")                                  # X1: offered
    row = page.locator("#party-accounts-tbody tr", has_text=f"Sister school {tag}")
    assert row.count() == 1
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator("#party-accounts").screenshot(path=os.path.join(ARTIFACTS, "party-accounts.png"))
    row.locator(".pa-pick").check()
    row.locator(".pa-type").select_option("client")
    with page.expect_response(lambda resp: "/entities/from-accounts" in resp.url) as made:
        page.click("#pa-make")
    assert made.value.status == 201                                          # X2: made
    party = page.locator("#entities-tbody tr", has_text=f"Sister school {tag}")
    party.wait_for()
    assert code in party.inner_text().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    wait_until(page, f"() => ![...document.querySelectorAll('#party-accounts-tbody tr')].some(r => r.textContent.includes('Sister school {tag}'))")

    party.locator(".view-entity-txns").click()                              # X3: its entries
    page.wait_for_selector("tr.entity-tx-row")
    rows = page.locator("tr.entity-tx-row")
    assert rows.count() == 1 and f"rent share {tag}" in rows.first.inner_text()
    assert "600" in rows.first.locator("td.ent-tx-balance").inner_text().replace(",", "")
    assert watch.problems() == [], watch.problems()
