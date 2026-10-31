"""Stock in the books, phase G3 (docs/PLAN_INVENTORY_GL.md; scenarios T15, T18, T20): the
credit note's «goods coming back» step and the Inventory page's stock count, under the
strict CSP.

Every flow user shares the default company, so these don't switch stock posting on (its
start date can never move again): the returns step reads a stock line the test serves,
and the count needs no posting at all."""
from __future__ import annotations

import json
import os
import uuid

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until

POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""


def _shot(page, name):
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, name))


def test_a_credit_note_asks_which_goods_come_back(flow_page):
    page, watch = flow_page("e2e_returns")
    run = uuid.uuid4().hex[:4]
    status, inv = page.evaluate(POST, ["/invoices", {"number": f"RET-{run}", "kind": "sales", "status": "issued",
                                                     "amount": 5400, "issue_date": "2026-10-01",
                                                     "due_date": "2026-10-31", "currency": "GBP"}])
    assert status == 201, inv
    line = {"item_id": str(uuid.uuid4()), "name": "Widget", "unit": "box", "moved": 15, "back": 5, "left": 10,
            "unit_price": 300, "unit_gross": 360}
    page.route(f"**/invoices/{inv['id']}/stock-returns", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"enabled": True, "number": inv["number"], "kind": "sales", "currency": "GBP", "lines": [line]})))
    try:
        page.evaluate("() => { location.hash = 'invoices'; }")
        page.wait_for_load_state("networkidle")
        row = page.locator("#invoices-tbody tr", has_text=inv["number"])
        row.wait_for()
        row.locator("details.row-menu summary").click()
        row.locator(".inv-credit-note").click()
        modal = page.locator("#cn-returns-modal")
        modal.wait_for(state="visible")
        expect(modal.locator("#cn-returns-body tr")).to_have_count(1)
        expect(modal.locator("#cn-returns-body tr")).to_contain_text("Widget")
        qty = modal.locator(".cn-ret-qty")
        qty.fill("11")                                                  # more than is left
        page.click("#cn-returns-ok")
        expect(page.locator("#alert")).to_contain_text("Only 10 Widget can come back.")
        qty.fill("3")
        expect(page.locator("#cn-returns-total")).to_contain_text("1,080.00")
        _shot(page, "credit-note-returns-en.png")
        page.click("#cn-returns-ok")
        page.locator("#ui-prompt-modal").wait_for(state="visible")
        assert page.input_value("#ui-prompt-input") == "1080"            # the goods at the invoice's prices
        page.click("#ui-prompt-ok")
        page.fill("#ui-prompt-input", "came back")
        with page.expect_request(lambda r: r.url.endswith("/credit-notes") and r.method == "POST") as sent:
            page.click("#ui-prompt-ok")
        body = sent.value.post_data_json
        assert body["amount"] == 1080 and body["returns"] == [{"item_id": line["item_id"], "quantity": 3}], body
        # the default company doesn't post stock: the server says so, in words
        expect(page.locator("#alert")).to_contain_text("Turn on stock in the books")

        # «No goods back» is a price credit, as before: nothing suggested, nothing returned
        row.locator("details.row-menu summary").click()
        row.locator(".inv-credit-note").click()
        modal.wait_for(state="visible")
        page.click("#cn-returns-none")
        page.locator("#ui-prompt-modal").wait_for(state="visible")
        assert page.input_value("#ui-prompt-input") == ""
        page.click("#ui-prompt-cancel")

        # in Persian
        switch_language(page, "fa")
        row = page.locator("#invoices-tbody tr", has_text=inv["number"])
        row.locator("details.row-menu summary").click()
        row.locator(".inv-credit-note").click()
        modal.wait_for(state="visible")
        expect(page.locator("#cn-returns-title")).to_have_text("کالای برگشتی")
        modal.locator(".cn-ret-qty").fill("3")
        expect(page.locator("#cn-returns-total")).to_contain_text("۱٬۰۸۰")
        _shot(page, "credit-note-returns-fa.png")
        page.click("#cn-returns-close")
        expect(modal).to_be_hidden()
        assert watch.problems() == [], watch.problems()
    except Exception:
        _shot(page, "credit-note-returns-failed.png")
        raise
    finally:
        switch_language(page, "en")


def test_a_stock_count_posts_what_differs(flow_page):
    page, watch = flow_page("e2e_returns")
    run = uuid.uuid4().hex[:5]
    names = {}
    for label, qty in (("Chair", 10), ("Desk", 11)):
        name = f"Count {label} {run}"
        status, item = page.evaluate(POST, ["/manager-reports/inventory/items", {"name": name, "unit": "unit"}])
        assert status == 201, item
        status, _mv = page.evaluate(POST, ["/manager-reports/inventory/movements", {
            "item_id": item["id"], "movement_type": "IN", "quantity": qty, "unit_cost": 50,
            "movement_date": "2026-10-01"}])
        assert status == 201, _mv
        names[label] = name
    try:
        page.locator('.nav-btn[data-page="inventory"]').first.click()
        page.wait_for_load_state("networkidle")
        with page.expect_response(lambda r: "/inventory/count-sheet" in r.url):
            page.click("#count-load")
        chair = page.locator("#count-sheet tr", has_text=names["Chair"])
        desk = page.locator("#count-sheet tr", has_text=names["Desk"])
        chair.locator(".count-qty").fill("8")
        desk.locator(".count-qty").fill("12")
        expect(chair.locator(".count-diff")).to_have_text("−2")
        expect(desk.locator(".count-diff")).to_have_text("+1")
        _shot(page, "stock-count-en.png")
        page.click("#count-post")
        page.locator("#ui-confirm-modal").wait_for(state="visible")
        expect(page.locator("#ui-confirm-message")).to_contain_text("2 items differ")
        with page.expect_response(lambda r: r.url.endswith("/inventory/counts") and r.request.method == "POST") as res:
            page.click("#ui-confirm-ok")
        assert res.value.status == 201, res.value.text()
        out = page.locator("#count-result")
        expect(out).to_contain_text("Count posted: 2 items adjusted")
        expect(out).to_contain_text(names["Chair"] + " −2")
        # the sheet again, with what is on hand now
        wait_until(page, "(n) => [...document.querySelectorAll('#count-sheet tr')].some(tr => tr.textContent.includes(n) && tr.children[1].textContent.trim().startsWith('8'))",
                   names["Chair"])
        switch_language(page, "fa")
        expect(page.locator("#count-post")).to_have_text("ثبت انبارگردانی")
        # the sheet and the result line again, in Persian
        expect(page.locator("#count-sheet th").first).to_have_text("کالا")
        expect(out).to_contain_text("انبارگردانی ثبت شد")   # the section's own word (glossary), not «شمارش»
        expect(out).not_to_contain_text("·")                 # the items joined with «، »: a dot beside «۲» read as «۰»
        expect(page.locator("#count-sheet tr", has_text=names["Chair"]).locator("td").nth(1)).to_contain_text("۸")
        _shot(page, "stock-count-fa.png")
        assert watch.problems() == [], watch.problems()
    except Exception:
        _shot(page, "stock-count-failed.png")
        raise
    finally:
        switch_language(page, "en")
