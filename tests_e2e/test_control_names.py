"""Every form control on every page has a name a screen reader can say: a
<label for>, a wrapping label, or an aria-label. 156 didn't — labels written
next to their input without for=, the line editors' cells (journal lines,
invoice lines, instalments, opening balances, PO lines), search boxes and
Settings' adjustment forms. A cell's control is named from its column header,
in the reader's language."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS, switch_language
from tests_e2e.test_mobile_layout import PAGES

NAMELESS = r"""() => [...document.querySelectorAll('.card[data-page] input, .card[data-page] select, .card[data-page] textarea')]
  .filter(e => e.offsetParent !== null && !['hidden', 'button', 'submit'].includes(e.type))
  .filter(e => !(e.labels && e.labels.length) && !(e.getAttribute('aria-label') || '').trim() && !e.getAttribute('aria-labelledby') && !e.title)
  .map(e => (e.id || e.name || e.tagName.toLowerCase()) + (e.placeholder ? ' (' + e.placeholder + ')' : ''))"""
# a button a screen reader can only call "button" ("✕", "×", an icon)
NAMELESS_BUTTONS = r"""() => [...document.querySelectorAll('.card[data-page] button')]
  .filter(b => b.offsetParent !== null)
  .filter(b => ((((b.getAttribute('aria-label') || '') + ' ' + (b.title || '') + ' ' + b.textContent).match(/[\p{L}\p{N}]/gu)) || []).length < 2)
  .map(b => 'button ' + (b.className || b.id) + ' «' + b.textContent.trim() + '»')"""
# something that opens on a click (a pointer cursor) but can't be reached with the keyboard
MOUSE_ONLY = r"""() => [...document.querySelectorAll('.card[data-page] *')]
  .filter(e => e.offsetParent !== null && !['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'SUMMARY', 'OPTION'].includes(e.tagName))
  .filter(e => !e.closest('button, a, label, summary') && getComputedStyle(e).cursor === 'pointer')
  .filter(e => !e.hasAttribute('tabindex') && !e.getAttribute('role'))
  .filter(e => !(e.parentElement && getComputedStyle(e.parentElement).cursor === 'pointer'))
  .map(e => e.tagName.toLowerCase() + '.' + String(e.className).split(' ')[0])"""
LINE_NAMES = r"""() => [...document.querySelectorAll('.card[data-page="transactions"] td input')]
  .filter(e => e.offsetParent !== null).map(e => e.getAttribute('aria-label') || '')"""


def _switch(page, lang):
    switch_language(page, lang)


def test_every_control_has_a_name(flow_page):
    page, watch = flow_page("e2e_a11y")
    try:
        nameless = {}
        for name in PAGES + ["settings"]:
            page.evaluate("(p) => { location.hash = p; }", name)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
            found = page.evaluate(NAMELESS) + page.evaluate(NAMELESS_BUTTONS) + page.evaluate(MOUSE_ONLY)
            if found:
                nameless[name] = found
        assert nameless == {}, nameless

        # the journal-line cells are named after their columns, and follow a language switch
        page.evaluate("() => { location.hash = 'transactions'; }")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        english = page.evaluate(LINE_NAMES)
        assert english and all(english), english
        _switch(page, "fa")
        for _ in range(25):   # the names follow the headers on the next animation frame
            persian = page.evaluate(LINE_NAMES)
            if persian != english:
                break
            page.wait_for_timeout(200)
        assert persian != english and all(any("؀" <= c <= "ۿ" for c in n) for n in persian if n), persian
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "control-names.png"), full_page=True)
        raise
    finally:
        _switch(page, "en")


def test_a_ledger_row_opens_from_the_keyboard(flow_page):
    page, watch = flow_page("e2e_keyboard")   # its own request budget: the sweep above spends e2e_a11y's
    posted = page.evaluate("""async () => {
        const accs = await (await fetch('/accounts')).json();
        const codes = accs.filter(a => a.code.length === 4).map(a => a.code).slice(0, 2);
        const r = await fetch('/transactions', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: new Date().toISOString().slice(0, 10), description: 'keyboard', reference: 'KB-1',
                lines: [{ account_code: codes[0], debit: 1000, credit: 0 }, { account_code: codes[1], debit: 0, credit: 1000 }] }) });
        return r.status; }""")
    assert posted == 201
    page.evaluate("() => { location.hash = 'ledger'; }")
    page.wait_for_load_state("networkidle")
    row = page.locator("tr.ledger-row").first
    row.wait_for(timeout=15_000)
    row.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#account-modal", state="visible", timeout=10_000)
    assert watch.problems() == [], watch.problems()
