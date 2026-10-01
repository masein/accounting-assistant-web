"""Sales and purchase manager reports: party-by-invoice and product rows,
the product filter (it used to be ignored — the Run button kept the
unwrapped runner, #224), purchase columns that mean something for a
purchase, and an empty period shown in the user's language."""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS

JS = """async ([path, body]) => {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return [r.status, await r.text()];
}"""


def _day(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _run(page, rt, frm, to, product=""):
    page.select_option("#mgr-report-type", rt)
    page.evaluate("""(d) => { for (const [id, v] of [['mgr-from-date', d[0]], ['mgr-to-date', d[1]], ['mgr-product-filter', d[2]]]) {
        const el = document.getElementById(id); if (el) { el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); } } }""",
                  [frm, to, product])
    before = page.locator("#mgr-report-preview").inner_html()
    with page.expect_response(lambda r: "/manager-reports/" in r.url and "trend" not in r.url):
        page.click("#mgr-run-btn")
    # the previous report's table is still there until this one draws: wait for the new one
    for _ in range(50):
        if page.locator("#mgr-report-preview").inner_html() != before:
            break
        page.wait_for_timeout(100)
    page.wait_for_selector("#mgr-report-preview table.mini-table")
    return ([h.strip() for h in page.locator("#mgr-report-preview th").all_inner_texts()],
            page.locator("#mgr-report-preview tbody").inner_text())


def test_sales_and_purchase_reports(flow_page):
    page, watch = flow_page("e2e_reports")
    tag = uuid.uuid4().hex[:5]
    product = f"Notebook {tag}"
    try:
        page.locator('.nav-btn[data-page="manager"]').first.click()
        page.wait_for_load_state("networkidle")
        currency = page.evaluate("() => document.getElementById('mgr-currency').value")
        ids = {}
        for kind, name in (("client", f"Aria {tag}"), ("supplier", f"Paper {tag}")):
            status, body = page.evaluate(JS, ["/entities", {"type": kind, "name": name}])
            assert status == 201, body
            ids[kind] = __import__("json").loads(body)["id"]
        for kind, party, items in (("sales", ids["client"], [(product, 3, 120_000), (f"Pen {tag}", 10, 15_000)]),
                                   ("purchase", ids["supplier"], [(product, 20, 60_000)])):
            inv = {"number": f"{kind[0].upper()}-{tag}", "kind": kind, "status": "issued", "entity_id": party,
                   "issue_date": _day(5), "due_date": _day(-25), "amount": sum(q * p for _n, q, p in items),
                   "items": [{"product_name": n, "quantity": q, "unit_price": p} for n, q, p in items]}
            if currency and currency != "ALL":
                inv["currency"] = currency
            status, body = page.evaluate(JS, ["/invoices", inv])
            assert status == 201, body

        heads, body = _run(page, "sales_by_product", _day(30), _day(0), product=product.lower())
        assert product in body and f"Pen {tag}" not in body                      # the filter reaches the server
        heads, body = _run(page, "purchase_by_product", _day(30), _day(0), product=product.lower())
        assert "Purchase amount" in heads and "Profit" not in heads and "Sales amount" not in heads, heads
        assert product in body and "1,200,000" in body
        heads, body = _run(page, "sales_by_invoice", _day(30), _day(0))
        assert "Invoice Id" not in heads and "Issue date" in heads and "Status" in heads, heads
        assert "Issued" in body and "…" not in body.split(f"S-{tag}")[0][-20:]
        # an empty period: its totals, labelled in words
        heads, body = _run(page, "purchase_by_invoice", _day(900), _day(800))
        assert "purchase_amount" not in body and "Purchase amount" in body, body
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "sales-reports.png"), full_page=True)
        raise
