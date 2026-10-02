"""An invoice row keeps its everyday actions on the row and the rest under
"⋯" (it carried up to 8 buttons on 3 lines: deep browser test, 2026-10-02,
#19). The menu opens on screen — even for the last row of a scrolling table
— its actions work, and Escape closes it."""
from __future__ import annotations

import uuid

from tests_e2e.conftest import wait_until

POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return r.status; }"""


def test_the_row_menu_opens_on_screen_and_its_actions_work(flow_page):
    page, watch = flow_page("e2e_rows")
    run = uuid.uuid4().hex[:4]
    for n in range(3):
        assert page.evaluate(POST, ["/invoices", {"number": f"ROW-{run}-{n}", "kind": "sales", "status": "issued",
                                                  "amount": 1000 + n, "issue_date": "2026-09-01", "due_date": "2026-10-01"}]) == 201
    page.evaluate("() => { location.hash = 'invoices'; }")
    page.wait_for_load_state("networkidle")
    row = page.locator("#invoices-tbody tr", has_text=f"ROW-{run}-0")
    row.wait_for()
    visible = row.locator(".row-actions > .btn:visible").all_inner_texts()
    assert len(visible) == 3, visible                      # payment, edit, PDF — the rest under ⋯
    assert row.locator("td bdi").first.evaluate("b => b.getClientRects().length") == 1   # the number on one line
    last = page.locator("#invoices-tbody tr").last
    last.locator("details.row-menu summary").click()
    # it is placed when the browser says it opened (a moment after the click)
    wait_until(page, """() => { const l = document.querySelector('details.row-menu[open] .row-menu-list[data-placed]');
        if (!l) return false; const r = l.getBoundingClientRect(); const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return r.top >= 0 && r.bottom <= window.innerHeight && !!el && l.contains(el); }""")
    page.keyboard.press("Escape")
    assert page.evaluate("() => !document.querySelector('details.row-menu[open]')")
    row.locator("details.row-menu summary").click()
    row.locator(".inv-credit-note").click()
    page.locator("#ui-prompt-modal").wait_for(state="visible")     # the credit note asks for its amount
    page.click("#ui-prompt-cancel")
    assert page.evaluate("() => !document.querySelector('details.row-menu[open]')")
    assert watch.problems() == [], watch.problems()
