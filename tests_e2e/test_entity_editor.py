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


def test_the_transactions_window_shows_the_newest_first_and_searches(browser):
    """U1–U3: newest first with each row's own balance; a search that matches Sepidar's «ي»,
    Persian or Latin digits and grouped amounts; the search survives an edit's return."""
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
        tag = uuid.uuid4().hex[:5]
        client = api("POST", "/entities", {"type": "client", "name": f"Search Client {tag}"}).json()
        codes = [a["code"] for a in api("GET", "/accounts").json() if len(a["code"]) >= 4][:2]
        for day, text, amount in (("2024-07-09", "واریز بابت افتتاح حساب", 1000000),
                                  ("2024-08-06", "بابت هزينه طراحي سربرگ تنخواه", 48478500),
                                  ("2024-07-29", "بابت پرداخت تیر ماه", 50000000)):
            r = api("POST", "/transactions", {
                "date": day, "description": text, "reference": f"IMPORT-sepidar-{tag}-{day}",
                "lines": [{"account_code": codes[0], "debit": amount, "credit": 0},
                          {"account_code": codes[1], "debit": 0, "credit": amount}],
                "entity_links": [{"role": "client", "entity_id": client["id"]}]})
            assert r.status == 201, r.text()
        page.reload()
        page.wait_for_load_state("networkidle")
        opener = page.locator(f".view-entity-txns[data-entity-id='{client['id']}']")
        opener.click()
        page.wait_for_selector("tr.entity-tx-row")
        said = page.eval_on_selector_all("tr.entity-tx-row", "rs => rs.map(r => r.dataset.q)")
        assert [d for q in said for d in ("2024-08-06", "2024-07-29", "2024-07-09") if d in q] == [
            "2024-08-06", "2024-07-29", "2024-07-09"]                              # U1: newest first
        rows = api("GET", f"/reports/entities/{client['id']}/transactions").json()
        balance = page.locator("tr.entity-tx-row").first.locator("td.ent-tx-balance").inner_text()
        digits = "".join(ch for ch in balance if ch.isdigit())
        assert digits.startswith(str(abs(int(rows[-1]["entity_balance"])))), balance  # today's balance on top
        card = page.locator("#account-modal .modal-card").bounding_box()
        for cell in ("td.ent-tx-balance", ".entity-tx-edit"):                   # U4: in view, no sideways scroll
            b = page.locator("tr.entity-tx-row").first.locator(cell).bounding_box()
            assert card["x"] <= b["x"] and b["x"] + b["width"] <= card["x"] + card["width"], (cell, b, card)

        def visible():
            return page.eval_on_selector_all("tr.entity-tx-row", "rs => rs.filter(r => !r.hidden).length")

        box = page.locator(".entity-tx-search")
        for needle in ("طراحی", "۴۸٬۴۷۸٬۵۰۰", "48478500", "48,478,500", f"sepidar-{tag}-2024-08"):
            box.fill(needle)
            assert visible() == 1, needle                                         # U2
        assert page.locator(".entity-tx-count").inner_text().strip() == "1 of 3"
        box.fill("zzz")
        assert visible() == 0 and page.locator("tr.entity-tx-none").is_visible()
        box.fill("")
        assert visible() == 3 and not page.locator("tr.entity-tx-none").is_visible()

        box.fill("تیر")                                                           # U3: an edit's return keeps it
        page.locator("tr.entity-tx-row:not([hidden]) .entity-tx-edit").first.click()
        page.wait_for_selector("#entity-tx-edit-cancel")
        page.click("#entity-tx-edit-cancel")
        page.wait_for_selector(".entity-tx-search")
        assert page.input_value(".entity-tx-search") == "تیر" and visible() == 1
        page.locator("#account-modal .modal-close").click()
        opener.click()
        page.wait_for_selector(".entity-tx-search")
        assert page.input_value(".entity-tx-search") == "" and visible() == 3   # from the list: a clean search
        assert not errors, errors
    finally:
        ctx.close()
