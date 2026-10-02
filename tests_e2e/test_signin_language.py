"""With nothing chosen on the device, the sign-in page speaks the browser's
language: a Persian browser met the English page and its English error (deep
browser test, 2026-10-02, finding #1). A language picked earlier still wins."""
from __future__ import annotations

import uuid

from tests_e2e.conftest import BASE_URL


def _login_page(browser, locale, saved=None):
    ctx = browser.new_context(locale=locale)
    if saved:
        ctx.add_init_script(f"try {{ localStorage.setItem('aa_ui_language', '{saved}'); }} catch (_) {{}}")
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/login")
    page.wait_for_load_state("networkidle")
    return ctx, page


def test_a_persian_browser_gets_the_persian_sign_in_page_and_errors(browser):
    ctx, page = _login_page(browser, "fa-IR")
    try:
        assert page.inner_text("#login-title") == "دستیار حسابداری"
        assert page.evaluate("() => [document.documentElement.lang, document.documentElement.dir]") == ["fa", "rtl"]
        page.fill("#username", f"nobody-{uuid.uuid4().hex[:6]}")
        page.fill("#password", "not-the-password-1")
        with page.expect_response(lambda r: r.url.endswith("/auth/login")):
            page.click("#submit-btn")
        page.locator("#error-box").wait_for(state="visible")
        assert page.inner_text("#error-box").strip() == "نام کاربری یا رمز عبور نادرست است"
    finally:
        ctx.close()


def test_another_language_or_a_saved_choice(browser):
    ctx, page = _login_page(browser, "de-DE")          # the app doesn't speak German: English
    try:
        assert page.evaluate("() => document.documentElement.lang") == "en"
    finally:
        ctx.close()
    ctx, page = _login_page(browser, "fa-IR", saved="es")
    try:
        assert page.evaluate("() => document.documentElement.lang") == "es"
        assert page.inner_text("#login-title") == "Asistente Contable"
    finally:
        ctx.close()
