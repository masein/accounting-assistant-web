"""The dashboard in tabs (deep browser test, 2026-10-02, #36: twelve sections
ran to 4,400 px). The KPIs, smart alerts and what changed stay on top; one
section opens under them; a section's own fetches wait for its tab."""
from __future__ import annotations

from tests_e2e.conftest import switch_language, wait_until

TABS = ["cash", "arap", "spend", "books"]
SELECTED = "() => document.querySelector('#dash-tabs [aria-selected=\"true\"]').dataset.tab"
SHOWN = "() => [...document.querySelectorAll('.dash-tabpanel')].filter(p => !p.hidden).map(p => p.id)"


def _open_dashboard(page):
    page.evaluate("() => { location.hash = 'dashboard'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#kpi-grid .kpi-card")


def _calls(page):
    """The section fetches the page made, in order."""
    seen = []

    def note(r):
        if r.method == "GET" and "/budgets/actual-vs-budget" in r.url:
            seen.append("budgets")
        elif r.method == "GET" and "/reports/missing-references" in r.url:
            seen.append("missing-references")
    page.on("request", note)
    return seen


def test_one_section_at_a_time_under_the_top(flow_page):
    page, watch = flow_page("e2e_dash_tabs")
    page.evaluate("() => { try { localStorage.removeItem('aa_dashboard_tab'); } catch (_) {} }")
    calls = _calls(page)
    _open_dashboard(page)
    # the top is always there, whatever the tab
    for sel in ("#kpi-grid", "#alerts-wrap", "#insights-wrap"):
        assert page.locator(sel).is_visible(), sel
    assert page.evaluate("() => [...document.querySelectorAll('#dash-tabs [role=tab]')].map(b => b.dataset.tab)") == TABS
    assert page.evaluate(SELECTED) == "cash" and page.evaluate(SHOWN) == ["dash-panel-cash"]
    assert page.locator("#forecast-wrap").is_visible() and not page.locator("#budget-wrap").is_visible()
    page.wait_for_timeout(300)
    assert calls == [], calls                                     # the budgets and missing references wait

    with page.expect_response(lambda r: "/budgets/actual-vs-budget" in r.url):
        page.click("#dash-tab-spend")
    assert page.evaluate(SHOWN) == ["dash-panel-spend"]
    wait_until(page, "() => document.querySelector('#budget-wrap').children.length > 0")
    for sel in ("#expense-category-wrap", "#vendor-spend-wrap", "#profitability-wrap", "#budget-wrap"):
        assert page.locator(sel).is_visible(), sel
    page.click("#dash-tab-arap")
    assert page.locator("#ar-aging-wrap").is_visible() and page.locator("#ap-aging-wrap").is_visible()
    with page.expect_response(lambda r: "/reports/missing-references" in r.url):
        page.click("#dash-tab-books")
    for sel in ("#health-wrap", "#checklist-wrap", "#owner-pack", "#missing-refs-wrap", "#snapshot-btn"):
        assert page.locator(sel).is_visible(), sel
    page.click("#dash-tab-spend")                                 # loaded already, nothing changed
    page.wait_for_timeout(300)
    assert calls == ["budgets", "missing-references"], calls

    # the tab is kept after a reload; its section loads, the others wait
    page.click("#dash-tab-books")
    calls.clear()
    page.reload()
    page.wait_for_load_state("networkidle")
    wait_until(page, "() => !document.getElementById('dash-panel-books').hidden")
    assert page.evaluate(SELECTED) == "books"
    wait_until(page, "() => document.querySelector('#missing-refs-wrap').children.length > 0")
    # back after another page: the open tab's section loads again, the rest when opened
    calls = _calls(page)
    page.evaluate("() => { location.hash = 'invoices'; }")
    page.wait_for_load_state("networkidle")
    calls.clear()
    _open_dashboard(page)
    page.wait_for_timeout(300)
    assert calls == ["missing-references"], calls
    with page.expect_response(lambda r: "/budgets/actual-vs-budget" in r.url):
        page.click("#dash-tab-spend")
    assert watch.problems() == [], watch.problems()


def test_the_tab_bar_is_a_tablist_in_both_directions(flow_page):
    page, watch = flow_page("e2e_dash_tabs")
    page.evaluate("() => { try { localStorage.setItem('aa_dashboard_tab', 'cash'); } catch (_) {} }")
    _open_dashboard(page)
    page.reload()
    page.wait_for_load_state("networkidle")
    bar = page.locator("#dash-tabs")
    assert bar.get_attribute("role") == "tablist" and bar.get_attribute("aria-label") == "Dashboard sections"
    for tab in TABS:
        b = page.locator(f"#dash-tab-{tab}")
        assert b.get_attribute("aria-controls") == f"dash-panel-{tab}"
        assert page.locator(f"#dash-panel-{tab}").get_attribute("aria-labelledby") == f"dash-tab-{tab}"
    try:
        page.focus("#dash-tab-cash")
        page.keyboard.press("ArrowRight")
        assert page.evaluate(SELECTED) == "arap"
        assert page.evaluate("() => document.activeElement.id") == "dash-tab-arap"
        assert page.evaluate("() => [...document.querySelectorAll('#dash-tabs [role=tab]')].map(b => b.tabIndex)") == [-1, 0, -1, -1]
        page.keyboard.press("End")
        assert page.evaluate(SELECTED) == "books"
        page.keyboard.press("ArrowRight")                         # wraps round
        assert page.evaluate(SELECTED) == "cash"
        page.keyboard.press("ArrowLeft")
        assert page.evaluate(SELECTED) == "books"
        page.keyboard.press("Home")
        assert page.evaluate(SELECTED) == "cash"
        page.keyboard.press("Tab")                                # into the open section
        assert page.evaluate("() => document.activeElement.id") == "dash-panel-cash"

        switch_language(page, "fa")
        assert page.inner_text("#dash-tab-cash") == "نقدینگی" and bar.get_attribute("aria-label") == "بخش‌های داشبورد"
        assert page.evaluate("() => getComputedStyle(document.getElementById('dash-tabs')).direction") == "rtl"
        page.focus("#dash-tab-cash")
        page.keyboard.press("ArrowLeft")                          # in RTL the next tab is to the left
        assert page.evaluate(SELECTED) == "arap"
        xs = page.evaluate("() => [...document.querySelectorAll('#dash-tabs [role=tab]')].map(b => b.getBoundingClientRect().left)")
        assert xs == sorted(xs, reverse=True), xs                 # laid out right to left
        page.keyboard.press("ArrowRight")
        assert page.evaluate(SELECTED) == "cash"
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_on_a_phone_the_bar_scrolls_and_the_page_does_not(flow_page):
    page, watch = flow_page("e2e_dash_tabs")
    page.set_viewport_size({"width": 360, "height": 780})
    try:
        switch_language(page, "fa")
        _open_dashboard(page)
        page.evaluate("() => document.getElementById('dash-tabs').scrollIntoView()")
        # four tabs don't fit: the far side fades, and the keyboard brings the last one in
        wait_until(page, "() => document.getElementById('dash-tabs').classList.contains('more-after')")
        page.focus("#dash-tab-cash")
        page.keyboard.press("End")
        inside = """(id) => { const b = document.getElementById('dash-tabs').getBoundingClientRect();
            const t = document.getElementById(id).getBoundingClientRect(); return t.left >= b.left - 1 && t.right <= b.right + 1; }"""
        wait_until(page, inside, "dash-tab-books")
        assert page.evaluate("() => document.getElementById('dash-tabs').classList.contains('more-before')")
        page.keyboard.press("Home")
        wait_until(page, inside, "dash-tab-cash")
        for tab in TABS:
            page.locator(f"#dash-tab-{tab}").scroll_into_view_if_needed()
            page.click(f"#dash-tab-{tab}")
            page.wait_for_load_state("networkidle")
            assert page.evaluate(SELECTED) == tab
            wide = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert wide <= 0, (tab, wide)
            box = page.locator(f"#dash-tab-{tab}").bounding_box()
            assert box["height"] >= 44, (tab, box)                # a finger-sized target
        assert watch.problems() == [], watch.problems()
    finally:
        page.set_viewport_size({"width": 1280, "height": 900})
        switch_language(page, "en")
