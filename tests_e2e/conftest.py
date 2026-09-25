"""Browser smoke suite (roadmap 2026-09 §6). Runs against a live server:
E2E_BASE_URL, E2E_USERNAME, E2E_PASSWORD. Not part of the unit suite
(pyproject testpaths = tests); CI runs it in its own job."""
from __future__ import annotations

import os

import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000").rstrip("/")
USERNAME = os.environ.get("E2E_USERNAME", "e2e_owner")
PASSWORD = os.environ.get("E2E_PASSWORD", "")
ARTIFACTS = os.environ.get("E2E_ARTIFACTS", "e2e-artifacts")


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="session")
def logged_in_state(browser):
    """Log in once through the real login form; later tests reuse the
    session cookies."""
    if not PASSWORD:
        pytest.skip("E2E_PASSWORD not set")
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click("#submit-btn")
    page.wait_for_url(lambda url: "/login" not in url, timeout=15_000)
    state = ctx.storage_state()
    ctx.close()
    return state


class PageWatch:
    """Collects JS exceptions, console errors and 5xx responses for one page."""

    def __init__(self, page):
        self.js_errors: list[str] = []
        self.console_errors: list[str] = []
        self.server_errors: list[str] = []
        self.csp_violations: list[str] = []
        page.on("pageerror", lambda e: self.js_errors.append(str(e)))
        page.on("console", self._console)
        page.on("response", lambda r: self.server_errors.append(f"{r.status} {r.url}") if r.status >= 500 else None)

    def _console(self, m) -> None:
        if m.type != "error":
            return
        self.console_errors.append(m.text)
        # The strict CSP refuses inline code with a console error, not an
        # exception — a button wired inline would just silently do nothing.
        if "Content Security Policy" in m.text:
            self.csp_violations.append(m.text)

    def problems(self) -> list[str]:
        # A 4xx fetch logs "Failed to load resource" in the console; those are
        # expected for role-gated endpoints. Exceptions, 5xx and CSP refusals
        # are never fine.
        return self.js_errors + self.server_errors + self.csp_violations


@pytest.fixture()
def app_page(browser, logged_in_state):
    ctx = browser.new_context(storage_state=logged_in_state, viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    watch = PageWatch(page)
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    yield page, watch
    ctx.close()
