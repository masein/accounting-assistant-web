"""The opening balances entry goes only with its word typed (scenarios BU1, BU4): on a party's transactions, its
delete asks for «opening» / «افتتاحیه»; a wrong word deletes nothing, the right one deletes it. A client, not a
bank: a bank made here put an English account name in the Persian pages' bank lists (test_persian_labels)."""
from __future__ import annotations

import os
import uuid

from tests_e2e.conftest import ARTIFACTS, BASE_URL, switch_language, wait_until


def test_the_opening_entry_needs_its_word(flow_page):
    page, watch = flow_page("e2e_opening")
    page.evaluate("() => { location.hash = 'entities'; }")
    page.wait_for_load_state("networkidle")
    csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "aa_csrf")
    api = lambda m, p, body=None: page.request.fetch(f"{BASE_URL}{p}", method=m, data=body,  # noqa: E731
                                                     headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"})
    tag = uuid.uuid4().hex[:5]
    party = api("POST", "/entities", {"type": "client", "name": f"Opening client {tag}"}).json()
    tx = api("POST", "/transactions", {
        "date": "2026-03-21", "description": f"Opening balances {tag}", "reference": "OPENING-BALANCES",
        "lines": [{"account_code": "1110", "debit": 5_000_000, "credit": 0},
                  {"account_code": "3110", "debit": 0, "credit": 5_000_000}],
        "entity_links": [{"role": "client", "entity_id": party["id"]}]})
    assert tx.status == 201, tx.text()
    tx_id = tx.json()["id"]
    os.makedirs(ARTIFACTS, exist_ok=True)
    try:
        for lang, word in (("en", "opening"), ("fa", "افتتاحیه")):
            if lang != "en":
                switch_language(page, lang)
            page.reload()
            page.evaluate("() => { location.hash = 'entities'; }")
            page.wait_for_load_state("networkidle")
            page.locator(f".view-entity-txns[data-entity-id='{party['id']}']").click()
            delete = page.locator(f".entity-tx-del[data-tx-id='{tx_id}']")
            delete.wait_for(timeout=15_000)
            delete.click()
            page.wait_for_selector("#ui-prompt-modal", state="visible")
            assert word in page.inner_text("#ui-prompt-label")
            page.locator("#ui-prompt-modal .modal-card").screenshot(path=os.path.join(ARTIFACTS, f"opening-delete-{lang}.png"))
            page.fill("#ui-prompt-input", "delete")                         # the wrong word
            page.click("#ui-prompt-ok")
            page.wait_for_timeout(600)
            assert api("GET", f"/transactions/{tx_id}").status == 200      # nothing deleted
            page.keyboard.press("Escape")
        delete = page.locator(f".entity-tx-del[data-tx-id='{tx_id}']")
        if not delete.is_visible():
            page.locator(f".view-entity-txns[data-entity-id='{party['id']}']").click()
            delete.wait_for(timeout=15_000)
        delete.click()
        page.wait_for_selector("#ui-prompt-modal", state="visible")
        page.fill("#ui-prompt-input", "افتتاحيه")                            # an Arabic «ي» is the same word
        with page.expect_response(lambda r: f"/transactions/{tx_id}" in r.url and r.request.method == "DELETE") as gone:
            page.click("#ui-prompt-ok")
        assert gone.value.status == 204
        wait_until(page, f"() => !document.querySelector(\".entity-tx-del[data-tx-id='{tx_id}']\")")
    finally:
        switch_language(page, "en")
        if api("GET", f"/transactions/{tx_id}").status == 200:
            api("DELETE", f"/transactions/{tx_id}?opening=confirm")
    assert watch.problems() == [], watch.problems()
