"""At phone width (390 px) no page scrolls sideways — in English or Persian.

A wide table scrolls inside its card and a form stacks into one column;
the Invoices tax-rate table, the purchase-order lines, the Products tabs,
Audit's threshold row, Migration's upload row and the Ledger's charts (only
drawn once there are journals) used to push the whole page wider than the
screen."""
from __future__ import annotations

import os
import uuid

import pytest

from tests_e2e.conftest import ARTIFACTS

PAGES = ["dashboard", "ai-accountant", "transactions", "invoices", "time", "expenses", "purchase-orders", "recurring",
         "commitments", "entities", "products", "inventory", "payroll", "equity", "fixed-assets", "petty-cash",
         "bank-statements", "ledger", "manager", "audit", "migration", "accounts"]


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_no_page_scrolls_sideways_on_a_phone(flow_page, lang):
    page, watch = flow_page(f"e2e_mobile_{lang}")  # one user per pass: 22 pages fill the 120/min budget
    try:
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("""(l) => { const s = document.getElementById('topbar-language'); s.value = l;
            s.dispatchEvent(new Event('change', { bubbles: true })); }""", lang)
        page.wait_for_load_state("networkidle")
        # The Ledger draws its charts only when there are journals — post a
        # couple with long descriptions so its widest state is the one checked.
        posted = page.evaluate("""async (ref) => {
            const res = await fetch('/accounts');
            const accs = await res.json();
            if (!Array.isArray(accs)) return [res.status, accs];
            const codes = accs.filter(a => a.code.length >= 4).map(a => a.code).slice(0, 2);
            const out = [];
            for (let i = 0; i < 2; i++) {
                const r = await fetch('/transactions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ date: '2026-09-0' + (i + 1), reference: ref + '-' + i,
                        description: 'Monthly office rent and service charge for the central branch ' + i,
                        lines: [{ account_code: codes[0], debit: 12500000, credit: 0 },
                                { account_code: codes[1], debit: 0, credit: 12500000 }] }) });
                out.push(r.status);
            }
            return out;
        }""", f"MOB-{uuid.uuid4().hex[:6]}")
        assert posted == [201, 201], posted
        wide = {}
        for name in PAGES:
            page.evaluate("(p) => { location.hash = p; }", name)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
            if name == "ledger":
                page.wait_for_selector("#charts-row", state="visible", timeout=15_000)
            over = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if over > 1:
                wide[name] = over
        assert wide == {}, wide
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, f"mobile-{lang}.png"), full_page=True)
        raise
    finally:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
