"""No page scrolls sideways on a tablet (768 px) either: the Ledger's toolbar
had four fixed-minimum columns, about 796 px (deep browser test, 2026-10-02,
#39). The phone check is test_mobile_layout.py."""
from __future__ import annotations

import uuid

from tests_e2e.conftest import switch_language
from tests_e2e.test_mobile_layout import PAGES


def test_no_page_scrolls_sideways_on_a_tablet(flow_page):
    page, watch = flow_page("e2e_tablet")
    try:
        page.set_viewport_size({"width": 768, "height": 1024})
        ref = f"TAB-{uuid.uuid4().hex[:6]}"
        posted = page.evaluate("""async (ref) => {
            const accs = await (await fetch('/accounts')).json();
            const codes = accs.filter(a => a.code.length >= 4).map(a => a.code).slice(0, 2);
            const r = await fetch('/transactions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ date: '2026-09-02', reference: ref, description: 'Monthly office rent and service charge for the central branch',
                    lines: [{ account_code: codes[0], debit: 12500000, credit: 0 }, { account_code: codes[1], debit: 0, credit: 12500000 }] }) });
            return r.status; }""", ref)
        assert posted == 201, posted
        # invoices too: their rows are the page's widest (in Persian they overflowed by 150 px)
        for n in range(3):
            st = page.evaluate("""async (n) => (await fetch('/invoices', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ number: n, kind: 'sales', status: 'issued', amount: 123456789,
                    issue_date: '2026-09-01', due_date: '2026-10-01' }) })).status""", f"{ref}-INVOICE-{n}")
            assert st == 201, st
        wide = {}
        for lang in ("en", "fa"):
            switch_language(page, lang)
            for name in PAGES:
                page.evaluate("(p) => { location.hash = p; }", name)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(250)
                page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
                over = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 1:
                    wide[f"{name} ({lang})"] = over
        assert wide == {}, wide
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
