"""The Ledger and chart-of-accounts filters treat ي/ی and ك/ک as one letter:
typing «موجودي» (an Arabic keyboard's ye) didn't find «موجودی نقد و بانک»."""
from __future__ import annotations


def test_the_filters_ignore_the_letterform(flow_page):
    page, watch = flow_page("e2e_persian")
    posted = page.evaluate("""async () => {
        const r = await fetch('/transactions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: new Date().toISOString().slice(0, 10), description: 'letters', reference: 'LF-1',
                lines: [{ account_code: '6112', debit: 1000, credit: 0 }, { account_code: '1110', debit: 0, credit: 1000 }] }) });
        return r.status; }""")
    assert posted == 201
    page.evaluate("() => { location.hash = 'ledger'; }")
    page.wait_for_load_state("networkidle")
    page.locator("tr.ledger-row").first.wait_for(timeout=15_000)
    page.fill("#ledger-search", "موجودي")
    page.wait_for_timeout(300)
    rows = page.locator("tr.ledger-row").all_inner_texts()
    assert rows and all("1110" in r for r in rows), rows
    page.evaluate("() => { location.hash = 'accounts'; }")
    page.wait_for_load_state("networkidle")
    page.fill("#coa-filter", "موجودي نقد")
    page.wait_for_timeout(300)
    assert "1110" in page.inner_text("#coa-tree")
    assert watch.problems() == [], watch.problems()
