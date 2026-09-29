"""What a page load fetches, per role (tests/test_boot_page_data.py pins the code).

Sign-in used to preload every page's lists for every role — about 40
requests, the owner dashboard twice, 14–18 refusals for a manager or an
employee. Now it fetches the shell and the page the role lands on, once.
"""
from __future__ import annotations

import pytest

from tests_e2e.conftest import BASE_URL, USERNAME, _flow_states, _log_in

# preloaded at every sign-in before; now only when their page opens
PAGE_ONLY = ("/reports/ledger-summary", "/invoices", "/recurring", "/manager-reports/inventory/items",
             "/ai-accountant/sessions", "/admin/users", "/admin/api-keys", "/admin/ai-usage", "/fx/rates")


def _open(browser, username):
    if username not in _flow_states:
        _flow_states[username] = _log_in(browser, username)
    ctx = browser.new_context(storage_state=_flow_states[username], viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    seen: list[tuple[int, str]] = []
    errors: list[str] = []

    def on_response(r):
        if r.url.startswith(BASE_URL) and "/static/" not in r.url:
            path = r.url[len(BASE_URL):]
            if path not in ("/", "/sw.js", "/manifest.webmanifest"):
                seen.append((r.status, path.split("?")[0]))
    page.on("response", on_response)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    return ctx, page, seen, errors


def _landed(page):
    return page.evaluate("document.querySelector('.card[data-page].active-page')?.getAttribute('data-page')")


@pytest.mark.parametrize("username,home", [
    (USERNAME, "dashboard"), ("e2e_cfo", "dashboard"), ("e2e_viewer", "dashboard"),
    ("e2e_manager", "expenses"), ("e2e_employee", "time"),
])
def test_a_page_load_fetches_the_shell_and_the_landing_page_once(browser, username, home):
    ctx, page, seen, errors = _open(browser, username)
    try:
        paths = [p for _, p in seen]
        assert _landed(page) == home
        assert not errors, errors
        assert paths.count("/auth/me") == 1 and paths.count("/fx/metadata") <= 1
        assert paths.count("/fx/reporting-currency") == 1
        assert paths.count("/reports/owner-dashboard") == (1 if home == "dashboard" else 0), paths
        assert not [p for p in paths if p in PAGE_ONLY], paths
        assert len(paths) <= 16, paths                                  # was 28–41
        refused = [p for s, p in seen if s == 403]
        if home == "dashboard":
            assert refused == [], refused                               # was 0–6
        else:
            # what's left is the landing page's own calls, not the preload
            assert len(refused) <= 4, refused                           # was 14–18
            assert "/reports/owner-dashboard" not in refused and "/invoices" not in refused
    finally:
        ctx.close()


def _go(page, name, wait_for=None):
    """Navigate like a nav click (hash change); wait for ``wait_for``'s response, else settle."""
    if wait_for:
        with page.expect_response(lambda r: wait_for in r.url, timeout=15_000):
            page.evaluate("(p) => { location.hash = '#' + p; }", name)
    else:
        page.evaluate("(p) => { location.hash = '#' + p; }", name)
    page.wait_for_timeout(1200)


def test_a_page_loads_its_data_when_it_opens_and_the_chat_restores_once(browser):
    ctx, page, seen, errors = _open(browser, "e2e_cfo")
    try:
        _go(page, "ledger", wait_for="/reports/ledger-summary")        # the ledger was only preloaded before
        assert _landed(page) == "ledger"
        assert page.locator("#results-tbody tr").count() >= 1           # a row, or the empty-state row
        seen.clear()
        _go(page, "ai-accountant", wait_for="/ai-accountant/sessions")
        assert [p for _, p in seen].count("/ai-accountant/sessions") == 1
        seen.clear()
        _go(page, "ledger", wait_for="/reports/ledger-summary")        # reloads each time it opens
        _go(page, "ai-accountant")
        assert "/ai-accountant/sessions" not in [p for _, p in seen]     # the chat restores once per load
        assert not errors, errors
    finally:
        ctx.close()
