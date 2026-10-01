"""Every page an accountant opens, in Persian, has its labels in Persian:
headings, buttons, form labels, table headers, empty states. English UI text
used to hide in the static page (Entities' filter bar, the Products hub,
Inventory's panels) and in rebuilt dropdowns — the translation files can't
catch text that never goes through them. Data (names, codes, amounts in table
cells) is left out; so are the handful of words that are English on purpose."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS

PAGES = ["dashboard", "ai-accountant", "transactions", "invoices", "time", "expenses", "purchase-orders", "recurring",
         "commitments", "entities", "products", "inventory", "payroll", "equity", "fixed-assets", "petty-cash",
         "bank-statements", "ledger", "manager", "audit", "migration", "accounts"]
SCAN = r"""() => {
  const out = new Set();
  const sel = ['th', 'button', 'label', 'summary', 'h1', 'h2', 'h3', 'h4', 'legend', 'p', 'strong', '.badge', '.empty-state', '.report-meta', 'option:checked'];
  const shown = e => (e.tagName === 'OPTION' ? e.parentElement.offsetParent !== null : e.offsetParent !== null);
  for (const e of document.querySelectorAll(sel.map(s => '.card[data-page] ' + s).join(', '))) {
    if (!shown(e) || e.closest('td') || e.closest('pre') || e.closest('code')) continue;
    const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    const txt = (own || (e.children.length ? '' : e.textContent)).trim();
    if (txt.length < 4) continue;
    const latin = (txt.match(/[A-Za-z]/g) || []).length, persian = (txt.match(/[؀-ۿ]/g) || []).length;
    if ((txt.match(/[A-Za-z]{3,}/g) || []).length >= 2 && latin > 3 * persian) out.add(txt.slice(0, 80));
  }
  return [...out];
}"""
ON_PURPOSE = ("CSV", "Excel", "PDF", "JSON", "IMAP", "INBOX", "IBAN", "API", "SMS", "VAT", "MTD", "HMRC", "TTMS",
              "GBP", "IRR", "USD", "EUR", "http", "@", "Telegram", "Bale", "Google", "Apple")


def test_every_page_speaks_persian(flow_page):
    page, watch = flow_page("e2e_persian")
    try:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'fa';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
        page.wait_for_load_state("networkidle")
        found = {}
        for name in PAGES:
            btn = page.locator(f'.nav-btn[data-page="{name}"]').first
            if not btn.count() or not btn.is_visible():
                continue
            btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            english = [x for x in page.evaluate(SCAN) if not any(w in x for w in ON_PURPOSE)]
            if english:
                found[name] = english
        assert found == {}, found
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "persian-labels.png"), full_page=True)
        raise
    finally:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
