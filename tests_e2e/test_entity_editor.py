"""Editing a journal from an entity's statement keeps its parties (hotfix for #187)."""
from __future__ import annotations

import uuid

from tests_e2e.conftest import BASE_URL, _flow_states, _log_in


def test_editing_a_journal_from_the_entity_statement_keeps_its_parties(browser):
    user = "e2e_cfo"
    if user not in _flow_states:
        _flow_states[user] = _log_in(browser, user)
    ctx = browser.new_context(storage_state=_flow_states[user], viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(f"{BASE_URL}/#entities")
        page.wait_for_load_state("networkidle")
        csrf = next(c["value"] for c in ctx.cookies() if c["name"] == "aa_csrf")
        api = lambda m, p, body=None: page.request.fetch(f"{BASE_URL}{p}", method=m, data=body,  # noqa: E731
                                                         headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"})
        name = f"Editor Client {uuid.uuid4().hex[:6]}"
        client = api("POST", "/entities", {"type": "client", "name": name}).json()
        codes = [a["code"] for a in api("GET", "/accounts").json() if len(a["code"]) >= 4][:2]
        tx = api("POST", "/transactions", {
            "date": "2026-09-01", "description": "linked sale", "reference": f"ED-{uuid.uuid4().hex[:5]}",
            "lines": [{"account_code": codes[0], "debit": 1000, "credit": 0},
                      {"account_code": codes[1], "debit": 0, "credit": 1000}],
            "entity_links": [{"role": "client", "entity_id": client["id"]}]})
        assert tx.status == 201, tx.text()
        tx_id = tx.json()["id"]
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator(f".view-entity-txns[data-entity-id='{client['id']}']").click()
        page.wait_for_selector(f".entity-tx-edit[data-tx-id='{tx_id}']", timeout=15_000)
        page.locator(f".entity-tx-edit[data-tx-id='{tx_id}']").click()
        page.wait_for_selector("#edit-tx-client")
        assert page.input_value("#edit-tx-client") == client["id"]          # the party is there, selected
        page.fill("#edit-tx-description", "linked sale (edited)")
        page.locator("#entity-tx-edit-form button[type='submit']").click()
        page.wait_for_timeout(1500)
        links = api("GET", f"/transactions/{tx_id}").json().get("entity_links") or []
        assert [(l.get("role"), l.get("entity_id")) for l in links] == [("client", client["id"])], links
        assert not errors, errors
    finally:
        ctx.close()
