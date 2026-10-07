"""A report that doesn't load says so (scenario D14): CEO and CFO Mode stayed
blank and only wrote a console warning when their report failed, which no
test saw. Now the page shows the server's own message (or "That did not
work"), and an exception is reported as uncaught, so the suites see it."""
from __future__ import annotations

from tests_e2e.conftest import BASE_URL, PageWatch, _flow_states, _log_in, wait_until

ALERT = "() => { const a = document.getElementById('alert'); return a && a.style.display !== 'none' && a.classList.contains('alert-error') ? a.innerText : ''; }"


def _page(browser):
    if "e2e_owner" not in _flow_states:
        _flow_states["e2e_owner"] = _log_in(browser, "e2e_owner")
    ctx = browser.new_context(storage_state=_flow_states["e2e_owner"], viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    watch = PageWatch(page)
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    return ctx, page, watch


def test_a_report_that_does_not_load_says_so(browser):
    ctx, page, watch = _page(browser)
    try:
        # the server refuses: its own words
        page.route("**/brain/ceo/report", lambda r: r.fulfill(
            status=503, content_type="application/json", body='{"detail": "Reports are being rebuilt, try again in a minute."}'))
        page.click('.nav-btn[data-page="ceo"]')
        wait_until(page, ALERT, timeout_ms=8_000)
        assert "Reports are being rebuilt" in page.evaluate(ALERT)

        # something unreadable: "That did not work", and the exception is reported
        page.evaluate("() => { document.getElementById('alert').style.display = 'none'; }")
        page.route("**/brain/cfo/report", lambda r: r.fulfill(status=200, content_type="application/json", body="not json"))
        page.click('.nav-btn[data-page="cfo"]')
        wait_until(page, ALERT, timeout_ms=8_000)
        assert page.evaluate(ALERT).strip().startswith(page.evaluate("() => t('msgFailed')"))
        assert any("JSON" in e or "json" in e for e in watch.js_errors), watch.js_errors   # the suites see it
    finally:
        ctx.close()
