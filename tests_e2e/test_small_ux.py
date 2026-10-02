"""An uploaded statement opens on its rows under a title in the user's words,
and a budget's category reads with its account's name (deep browser test,
2026-10-02, #27 and #28)."""
from __future__ import annotations

from tests_e2e.conftest import switch_language, wait_until

CSV = ("Date,Description,Debit,Credit,Balance\n"
       "2026-09-01,Opening transfer,0,9000000,9000000\n"
       "2026-09-03,Office rent,2500000,0,6500000\n").encode()
POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""


def test_an_upload_opens_its_rows_and_a_budget_names_its_account(flow_page):
    page, watch = flow_page("e2e_ux")
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'bank-statements'; }")
        page.wait_for_load_state("networkidle")
        page.set_input_files("#bs-file-input", files=[{"name": "ux.csv", "mimeType": "text/csv", "buffer": CSV}])
        with page.expect_response(lambda r: "/bank-statements/upload" in r.url):
            page.click("#bs-upload-btn")
        wait_until(page, "() => getComputedStyle(document.getElementById('bs-detail-wrap')).display !== 'none'"
                         " && document.querySelectorAll('#bs-rows-body tr').length >= 2")
        title = page.inner_text("#bs-detail-title")
        assert title == "ux.csv (2 ردیف)", title          # no "Unknown —", no English "rows"

        accounts = page.evaluate("async () => (await (await fetch('/accounts')).json()).map(a => [a.code, a.name])")
        code, name = next((c, n) for c, n in accounts if c[0] in "56789" and not c.startswith("91") and len(c) >= 4)
        month = page.evaluate("() => currentMonthKey()")
        status, body = page.evaluate(POST, ["/budgets", {"month": month, "category": code, "limit_amount": 1000}])
        assert status in (200, 201), body
        page.evaluate("() => { location.hash = 'dashboard'; }")
        page.wait_for_load_state("networkidle")
        wait_until(page, "(c) => [...document.querySelectorAll('#budget-wrap td')].some(td => td.textContent.startsWith(c + ' — '))", code)
        assert page.evaluate("(c) => [...document.querySelectorAll('#budget-category-list option')].some(o => o.value === c)", code)
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
