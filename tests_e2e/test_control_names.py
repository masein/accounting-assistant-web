"""Every form control on every page has a name a screen reader can say: a
<label for>, a wrapping label, or an aria-label. 156 didn't — labels written
next to their input without for=, the line editors' cells (journal lines,
invoice lines, instalments, opening balances, PO lines), search boxes and
Settings' adjustment forms. A cell's control is named from its column header,
in the reader's language."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS
from tests_e2e.test_mobile_layout import PAGES

NAMELESS = r"""() => [...document.querySelectorAll('.card[data-page] input, .card[data-page] select, .card[data-page] textarea')]
  .filter(e => e.offsetParent !== null && !['hidden', 'button', 'submit'].includes(e.type))
  .filter(e => !(e.labels && e.labels.length) && !(e.getAttribute('aria-label') || '').trim() && !e.getAttribute('aria-labelledby') && !e.title)
  .map(e => (e.id || e.name || e.tagName.toLowerCase()) + (e.placeholder ? ' (' + e.placeholder + ')' : ''))"""
LINE_NAMES = r"""() => [...document.querySelectorAll('.card[data-page="transactions"] td input')]
  .filter(e => e.offsetParent !== null).map(e => e.getAttribute('aria-label') || '')"""


def _switch(page, lang):
    page.evaluate("""(l) => { const s = document.getElementById('topbar-language'); s.value = l;
        s.dispatchEvent(new Event('change', { bubbles: true })); }""", lang)
    page.wait_for_load_state("networkidle")


def test_every_control_has_a_name(flow_page):
    page, watch = flow_page("e2e_a11y")
    try:
        nameless = {}
        for name in PAGES + ["settings"]:
            page.evaluate("(p) => { location.hash = p; }", name)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
            found = page.evaluate(NAMELESS)
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
