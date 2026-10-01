"""The Debtor/Creditor manager report shows each side, party by party with its
aging, and draws its charts. It used to show only two raw totals ("debtors",
"creditors") — the page still read an old response shape — and never drew.
Under the strict CSP, without a console error."""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS, wait_until

JS_POST = """async ([day, lines, link, currency]) => {
  const body = { date: day, description: link.name, lines, entity_links: [link] };
  if (currency && currency !== 'ALL') body.currency = currency;
  const r = await fetch('/transactions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body) });
  return [r.status, await r.text()];
}"""


def test_both_sides_party_by_party(flow_page):
    page, watch = flow_page("e2e_reports")
    tag = uuid.uuid4().hex[:6]
    client, supplier = f"Client {tag}", f"Supplier {tag}"
    day = (date.today() - timedelta(days=3)).isoformat()
    try:
        # the entries go in the currency the report opens in (the company's most-used one)
        page.locator('.nav-btn[data-page="manager"]').first.click()
        page.wait_for_load_state("networkidle")
        currency = page.evaluate("() => document.getElementById('mgr-currency').value")
        # the e2e company carries the Iranian seed chart: 1112 receivables, 2110 payables, 2130 VAT payable
        for lines, link in (
            ([{"account_code": "1112", "debit": 1200, "credit": 0}, {"account_code": "4110", "debit": 0, "credit": 1200}],
             {"role": "client", "name": client}),
            ([{"account_code": "6112", "debit": 450, "credit": 0}, {"account_code": "2110", "debit": 0, "credit": 450}],
             {"role": "supplier", "name": supplier}),
            ([{"account_code": "6112", "debit": 90, "credit": 0}, {"account_code": "2130", "debit": 0, "credit": 90}],
             {"role": "supplier", "name": supplier}),                      # VAT: not owed to the supplier
        ):
            status, body = page.evaluate(JS_POST, [day, lines, link, currency])
            assert status == 201, body
        page.select_option("#mgr-report-type", "debtor_creditor")
        page.evaluate("""(d) => { for (const [id, v] of [['mgr-from-date', d[0]], ['mgr-to-date', d[1]]]) {
            const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); } }""",
                      [(date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()])
        with page.expect_response(lambda r: "/manager-reports/operational/debtor-creditor" in r.url):
            page.click("#mgr-run-btn")
        page.wait_for_selector("#mgr-report-preview .panel table.mini-table")
        panels = page.locator("#mgr-report-preview .panel")
        assert panels.count() == 2
        debtors, creditors = panels.nth(0).inner_text(), panels.nth(1).inner_text()
        assert client in debtors and client not in creditors
        row = panels.nth(1).locator("tr", has_text=supplier)
        assert row.locator("td").last.inner_text().replace(",", "").strip() == "450"   # not 540
        assert page.locator("#mgr-report-preview th").first.inner_text().strip()          # headed columns
        # the charts draw again (the donut at least)
        wait_until(page, "() => document.querySelectorAll('#mgr-extra-charts canvas, .report-chart-panel canvas').length > 0")
        # the export's table has one row per party with its side
        headers, rows = page.evaluate("() => { const d = reportToTableData(lastManagerReport); return [d.headers, d.rows]; }")
        assert headers[0] == "role" and ["debtor", client] in [r[:2] for r in rows] and ["creditor", supplier] in [r[:2] for r in rows]
        assert watch.problems() == [], watch.problems()
    except Exception:                                   # a timeout too: keep what the page showed
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "debtor-creditor.png"), full_page=True)
        raise


JS_INVOICE = """async ([vendor, body]) => {
  const e = await fetch('/entities', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'supplier', name: vendor }) });
  if (e.status !== 201) return [e.status, await e.text()];
  body.entity_id = (await e.json()).id;
  const r = await fetch('/invoices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return [r.status, await r.text()];
}"""


def test_accounts_payable_runs_from_the_run_button(flow_page):
    """The Run button used to keep the unwrapped report runner, so "Accounts
    payable" showed the balance sheet."""
    page, watch = flow_page("e2e_reports")
    vendor = f"Vendor {uuid.uuid4().hex[:6]}"
    try:
        page.locator('.nav-btn[data-page="manager"]').first.click()
        page.wait_for_load_state("networkidle")
        currency = page.evaluate("() => document.getElementById('mgr-currency').value")
        body = {"number": f"B-{uuid.uuid4().hex[:6]}", "kind": "purchase", "status": "issued",
                "issue_date": (date.today() - timedelta(days=40)).isoformat(),
                "due_date": (date.today() - timedelta(days=10)).isoformat(), "amount": 750_000}
        if currency and currency != "ALL":
            body["currency"] = currency
        status, text = page.evaluate(JS_INVOICE, [vendor, body])
        assert status == 201, text
        page.select_option("#mgr-report-type", "accounts_payable")
        page.evaluate("(d) => { const el = document.getElementById('mgr-to-date'); el.value = d; el.dispatchEvent(new Event('change', { bubbles: true })); }",
                      date.today().isoformat())
        with page.expect_response(lambda r: "/manager-reports/operational/accounts-payable" in r.url):
            page.click("#mgr-run-btn")
        page.wait_for_selector("#mgr-report-preview table.mini-table")
        preview = page.locator("#mgr-report-preview").inner_text()
        assert vendor in preview and "Assets" not in preview, preview[:300]
        row = page.locator("#mgr-report-preview tr", has_text=vendor)
        assert "750,000" in row.inner_text() and "1-30" in row.inner_text()
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "accounts-payable.png"), full_page=True)
        raise
