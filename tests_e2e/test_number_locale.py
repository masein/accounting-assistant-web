"""Numbers read the same on every browser (scenario I12): a Persian or a
Spanish browser shows 1,234,567 — not «۱٬۲۳۴٬۵۶۷» or «1.234.567» — in what
used a bare toLocaleString(), here the chart drill-down's lines and total."""
from __future__ import annotations

from tests_e2e.conftest import BASE_URL, PageWatch, _flow_states, _log_in

DRILL = r"""() => { showChartDrilldown('Balance Sheet', 'Equity',
  [{ code: '3110', name: 'Capital', balance: 1234567 }, { code: '', name: 'Result', balance: -1000 }]);
  return document.getElementById('chart-modal-body').innerText; }"""


def test_amounts_have_latin_digits_and_comma_groups_on_any_browser(browser):
    if "e2e_owner" not in _flow_states:
        _flow_states["e2e_owner"] = _log_in(browser, "e2e_owner")
    for locale in ("fa-IR", "es-ES"):
        ctx = browser.new_context(storage_state=_flow_states["e2e_owner"], locale=locale)
        page = ctx.new_page()
        watch = PageWatch(page)
        try:
            page.goto(f"{BASE_URL}/")
            page.wait_for_load_state("networkidle")
            text = page.evaluate(DRILL)
            assert "1,234,567" in text and "1,233,567" in text, (locale, text)     # a line, and the total
            assert not any(ch in text for ch in "۰۱۲۳۴۵۶۷۸۹") and "1.234.567" not in text, (locale, text)
            assert watch.problems() == []
        finally:
            ctx.close()
