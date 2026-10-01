"""Every page an accountant opens, in Persian, has its labels in Persian:
headings, buttons, form labels, table headers, empty states. English UI text
used to hide in the static page (Entities' filter bar, the Products hub,
Inventory's panels) and in rebuilt dropdowns — the translation files can't
catch text that never goes through them. Data (names, codes, amounts in table
cells) is left out; so are the handful of words that are English on purpose.

What charts draw is checked too (legends, titles, axis categories live on a
canvas, out of reach of a DOM scan), as are the Ledger's KPI captions and the
CEO / CFO pages, which an accountant can't open — a CFO signs in for those."""
from __future__ import annotations

import os
import uuid

from tests_e2e.conftest import ARTIFACTS

PAGES = ["dashboard", "ai-accountant", "transactions", "invoices", "time", "expenses", "purchase-orders", "recurring",
         "commitments", "entities", "products", "inventory", "payroll", "equity", "fixed-assets", "petty-cash",
         "bank-statements", "ledger", "manager", "audit", "migration", "accounts"]
SCAN = r"""() => {
  const out = new Set();
  const sel = ['th', 'button', 'label', 'summary', 'h1', 'h2', 'h3', 'h4', 'legend', 'p', 'strong', '.badge', '.empty-state', '.report-meta', 'option:checked', '.ledger-kpi .k'];
  const shown = e => (e.tagName === 'OPTION' ? e.parentElement.offsetParent !== null : e.offsetParent !== null);
  for (const e of document.querySelectorAll(sel.map(s => '.card[data-page] ' + s).join(', '))) {
    if (!shown(e) || e.closest('td') || e.closest('pre') || e.closest('code')) continue;
    // a picked record ("Supplier bed332", an id as its value) is data, not a label
    if (e.tagName === 'OPTION' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(e.value)) continue;
    const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    const txt = (own || (e.children.length ? '' : e.textContent)).trim();
    if (txt.length < 4) continue;
    const latin = (txt.match(/[A-Za-z]/g) || []).length, persian = (txt.match(/[؀-ۿ]/g) || []).length;
    if ((txt.match(/[A-Za-z]{3,}/g) || []).length >= 2 && latin > 3 * persian) out.add(txt.slice(0, 80));
    // a one-word column header ("Category", "Vendor") is a label too
    else if (e.tagName === 'TH' && /^[A-Za-z][A-Za-z ]{3,}$/.test(txt)) out.add(txt);
  }
  return [...out];
}"""
# Text Chart.js draws on its canvas: dataset legends and the title — and, when
# asked, the category labels (on most charts those are data: item names,
# accounts, months; on CEO mode's they are the app's own words).
CHART_TEXT = r"""(withLabels) => typeof Chart === 'undefined' ? [] : Object.values(Chart.instances)
  .filter(c => c.canvas.offsetParent !== null)
  .flatMap(c => [...c.data.datasets.map(d => d.label), c.options.plugins && c.options.plugins.title && c.options.plugins.title.text,
                 ...(withLabels ? c.data.labels || [] : [])])
  .filter(x => typeof x === 'string' && /[A-Za-z]{3,}/.test(x) && !/[\u0600-\u06FF]/.test(x))"""
# "0 mo", "13,500,000 GBP/mo", "0 months" on the executive pages
UNITS = r"""() => [...document.querySelectorAll('.card[data-page] *')]
  .filter(e => e.offsetParent !== null && !e.children.length && /\b(mo|months)\b/.test(e.textContent))
  .map(e => e.textContent.trim().slice(0, 60))"""
# journals in more than one month, so the Ledger, the dashboard and the CEO
# page all have something to chart whatever ran before
POST_JOURNALS = r"""async (ref) => {
  const accs = await (await fetch('/accounts')).json();
  const expense = accs.filter(a => a.code.length === 4 && a.code[0] === '6').map(a => a.code);
  const today = new Date();
  const out = [];
  for (let i = 0; i < 2; i++) {
    const d = (i ? new Date(Date.UTC(today.getFullYear(), today.getMonth() - 1, 5)) : today).toISOString().slice(0, 10);
    const r = await fetch('/transactions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: d, reference: ref + '-' + i, description: 'office rent ' + i,
        lines: [{ account_code: expense[i % expense.length], debit: 9000000, credit: 0 },
                { account_code: '1110', debit: 0, credit: 9000000 }] }) });
    out.push(r.status);
  }
  return out;
}"""
ON_PURPOSE = ("CSV", "Excel", "PDF", "JSON", "IMAP", "INBOX", "IBAN", "API", "SMS", "VAT", "MTD", "HMRC", "TTMS",
              "GBP", "IRR", "USD", "EUR", "http", "@", "Telegram", "Bale", "Google", "Apple")


def test_every_page_speaks_persian(flow_page):
    page, watch = flow_page("e2e_persian")
    try:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'fa';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
        page.wait_for_load_state("networkidle")
        assert page.evaluate(POST_JOURNALS, f"FA-{uuid.uuid4().hex[:6]}") == [201, 201]
        page.reload()   # the dashboard drew before there was anything to show
        page.wait_for_load_state("networkidle")
        found = {}
        for name in PAGES:
            btn = page.locator(f'.nav-btn[data-page="{name}"]').first
            if not btn.count() or not btn.is_visible():
                continue
            btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            english = [x for x in page.evaluate(SCAN) + page.evaluate(CHART_TEXT, False) if not any(w in x for w in ON_PURPOSE)]
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


def test_the_executive_pages_speak_persian(flow_page):
    """CEO and CFO mode: KPI cards, chart legends, months — in Persian."""
    page, watch = flow_page("e2e_cfo")
    try:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'fa';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
        page.wait_for_load_state("networkidle")
        assert page.evaluate(POST_JOURNALS, f"FX-{uuid.uuid4().hex[:6]}") == [201, 201]
        found = {}
        for name in ("ceo", "cfo"):
            page.locator(f'.nav-btn[data-page="{name}"]').first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            if name == "ceo":   # its four charts are drawn, so their text is checked
                assert page.evaluate("() => Object.values(Chart.instances).filter(c => c.canvas.offsetParent !== null).length") == 4
            english = [x for x in page.evaluate(SCAN) + page.evaluate(CHART_TEXT, True) + page.evaluate(UNITS)
                       if not any(w in x for w in ON_PURPOSE)]
            if english:
                found[name] = english
        assert found == {}, found
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "persian-executive.png"), full_page=True)
        raise
    finally:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
