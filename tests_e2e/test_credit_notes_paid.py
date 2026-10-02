"""A paid invoice can be credited; the part beyond what was owed is the
customer's credit, kept or refunded, and it pays their next invoice (deep
browser test, 2026-10-02, finding #44). The history reads in the user's
language."""
from __future__ import annotations

import os
import re
import uuid

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until

POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""


def _row(page, number):
    row = page.locator("#invoices-tbody tr", has_text=number)
    row.wait_for()
    return row


def _menu(row, cls):
    row.locator("details.row-menu summary").click()
    row.locator(cls).click()


def test_credit_a_paid_invoice_keep_use_and_refund_the_credit(flow_page):
    page, watch = flow_page("e2e_credits")
    try:
        run = uuid.uuid4().hex[:4]
        status, client = page.evaluate(POST, ["/entities", {"type": "client", "name": f"Credit client {run}"}])
        assert status in (200, 201), client
        def invoice(n):
            st, inv = page.evaluate(POST, ["/invoices", {"number": f"CR-{run}-{n}", "kind": "sales", "status": "issued",
                                                         "amount": 1000000, "issue_date": "2026-09-01", "due_date": "2026-10-01",
                                                         "entity_id": client["id"]}])
            assert st == 201, inv
            return inv
        first, third = invoice(1), invoice(3)
        for inv in (first, third):
            assert page.evaluate(POST, [f"/invoices/{inv['id']}/payments", {"amount": 1000000}])[0] == 201
        page.evaluate("() => { location.hash = 'invoices'; }")
        page.wait_for_load_state("networkidle")

        # a paid invoice: credit 400,000 and keep it as the customer's credit
        _menu(_row(page, first["number"]), ".inv-credit-note")
        page.fill("#ui-prompt-input", "400000"); page.click("#ui-prompt-ok")
        page.fill("#ui-prompt-input", "returned"); page.click("#ui-prompt-ok")
        page.locator("#ui-confirm-modal").wait_for(state="visible")       # refund now, or keep?
        page.click("#ui-confirm-cancel")                                   # keep as credit
        wait_until(page, "(n) => [...document.querySelectorAll('#invoices-tbody tr')].some(tr => tr.textContent.includes(n) && /400,000/.test(tr.textContent))",
                   first["number"])

        # the next invoice: the payment offers the credit first
        second = invoice(2)
        page.evaluate("() => loadInvoices()")
        row2 = _row(page, second["number"])
        wait_until(page, "(n) => (_invoicesCache.find(i => i.number === n) || {}).party_credit === 400000", second["number"])
        row2.locator(".inv-payment").click()
        page.locator("#ui-confirm-modal").wait_for(state="visible")
        with page.expect_response(lambda r: "/apply-credit" in r.url):
            page.click("#ui-confirm-ok")
        page.locator("#ui-prompt-modal").wait_for(state="visible")         # and money for the rest
        assert page.input_value("#ui-prompt-input") == "600000"
        page.click("#ui-prompt-cancel")
        got = page.evaluate("async (id) => (await (await fetch('/invoices')).json()).find(i => i.id === id)", second["id"])
        assert got["status"] == "partially_paid" and got["balance_due"] == 600000 and got["party_credit"] == 0

        # another paid invoice: credit 250,000 and refund it now
        _menu(_row(page, third["number"]), ".inv-credit-note")
        page.fill("#ui-prompt-input", "250000"); page.click("#ui-prompt-ok")
        page.fill("#ui-prompt-input", ""); page.click("#ui-prompt-ok")
        page.locator("#ui-confirm-modal").wait_for(state="visible")
        with page.expect_response(lambda r: "/credit-notes" in r.url and r.request.method == "POST") as res:
            page.click("#ui-confirm-ok")                                   # refund now
        assert res.value.status == 201
        got = page.evaluate("async (id) => (await (await fetch('/invoices')).json()).find(i => i.id === id)", third["id"])
        assert got["status"] == "paid" and got["credited"] == 250000 and got["credit_available"] == 0

        # the history, in Persian
        switch_language(page, "fa")
        _menu(_row(page, first["number"]), ".inv-timeline")
        page.locator("#ui-confirm-modal").wait_for(state="visible")
        history = page.inner_text("#ui-confirm-message")
        assert "برگ بستانکار" in history and "بستانکاری مشتری" in history and "صرف فاکتور" in history, history
        assert not re.search(r"credit_note|payment|\bGBP\b \d|T\d\d:", history), history
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "credit-history-fa.png"))
        page.click("#ui-confirm-ok")
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
