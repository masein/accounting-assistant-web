"""Every page an owner can open renders without a JS exception or a server
error, in English and in Persian (RTL)."""
from __future__ import annotations

import os

import pytest

from tests_e2e.conftest import ARTIFACTS, BASE_URL, PageWatch

PAGES = [
    "dashboard", "ai-accountant", "transactions", "ledger", "invoices", "entities", "time", "expenses",
    "payroll", "purchase-orders", "recurring", "commitments", "bank-statements", "manager", "cfo", "ceo",
    "equity", "inventory", "products", "petty-cash", "audit", "migration", "settings",
]


def _shot(page, name):
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, f"{name}.png"), full_page=True)


def test_login_page_loads_clean(browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    watch = PageWatch(page)
    page.goto(f"{BASE_URL}/login")
    page.wait_for_load_state("networkidle")
    assert page.locator("#login-form").is_visible()
    assert watch.problems() == []
    ctx.close()


@pytest.mark.parametrize("name", PAGES)
def test_page_renders_clean(app_page, name):
    page, watch = app_page
    button = page.locator(f'.nav-btn[data-page="{name}"]').first
    if button.count() == 0 or not button.is_visible():
        pytest.skip(f"{name} is not in this role's navigation")
    button.click()
    page.wait_for_load_state("networkidle")
    card = page.locator(f'.card[data-page="{name}"]').first
    try:
        assert card.is_visible(), f"{name} did not show its page"
        assert watch.problems() == [], f"{name}: {watch.problems()}"
    except AssertionError:
        _shot(page, name)
        raise


def test_persian_is_right_to_left_and_invoices_render(app_page):
    page, watch = app_page
    # The top-bar language picker (inside the closed user menu): set it and
    # fire its change handler, as choosing an option does.
    page.evaluate("""() => { const s = document.getElementById('topbar-language');
        s.value = 'fa'; s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    page.wait_for_load_state("networkidle")
    page.locator('.nav-btn[data-page="invoices"]').first.click()
    page.wait_for_load_state("networkidle")
    direction = page.evaluate("document.documentElement.dir || getComputedStyle(document.body).direction")
    try:
        assert direction == "rtl"
        assert page.locator('.card[data-page="invoices"]').first.is_visible()
        assert watch.problems() == []
    except AssertionError:
        _shot(page, "fa-invoices")
        raise


def test_the_watch_really_catches_errors(app_page):
    """A clean run means something only if an error would have been seen."""
    page, watch = app_page
    page.evaluate("setTimeout(() => { throw new Error('smoke self-test') }, 0)")
    page.route("**/__e2e_boom", lambda route: route.fulfill(status=500, body="boom"))
    page.evaluate("fetch('/__e2e_boom').catch(() => {})")
    page.wait_for_timeout(500)
    problems = " | ".join(watch.problems())
    assert "smoke self-test" in problems and "500" in problems
