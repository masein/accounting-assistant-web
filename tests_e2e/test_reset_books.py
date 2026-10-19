"""A school's books cleared from the companies console, to import them again (scenarios AI1–AI3).

The server's side is tests/test_books_reset.py; here the console's replies are served by the test
and the page is told it is the platform admin's, as signing in as one would."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

NAME = "مدرسه منادی حق"
COMPANY = {"id": "c0ffee00-0000-4000-8000-000000000a11", "name": NAME, "locale": "ir", "kind": "business",
           "base_currency": "IRR", "login_username": "manadi", "status": "active", "has_logo": False}
BOOKS = {"name": NAME, "tables": {"transactions": 251, "transaction_lines": 640, "accounts": 154, "entities": 12,
                                  "ai_chat_sessions": 3}, "settings": ["journal_import_account_map"], "rows": 1060}


def _serve(page, url, body, method=None):
    def handle(route):
        if method and route.request.method != method:
            return route.fallback()
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
    page.route(url, handle)


def test_a_schools_books_are_cleared_once_its_name_is_typed(flow_page):
    page, watch = flow_page("e2e_wipe")
    posted = []
    try:
        switch_language(page, "fa")
        _serve(page, "**/admin/companies", [COMPANY], method="GET")
        _serve(page, "**/admin/companies/ai-usage", [])
        _serve(page, "**/admin/companies/ai-review", [])
        _serve(page, f"**/admin/companies/{COMPANY['id']}/books", BOOKS)
        page.route(f"**/admin/companies/{COMPANY['id']}/reset-books", lambda route: (
            posted.append(route.request.post_data_json),
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"rows": 1060, "removed": BOOKS["tables"], "accounts_seeded": 120}))))
        page.evaluate("() => { isSuperadmin = true; location.hash = 'companies'; }")
        page.wait_for_load_state("networkidle")
        page.evaluate("() => loadCompanies()")
        row = page.locator("#companies-tbody tr").filter(has_text=NAME)
        row.get_by_role("button", name="پاک کردن دفاتر").click()
        modal = page.locator("#ui-prompt-modal")
        expect(modal).to_be_visible()
        label = modal.locator("#ui-prompt-label")
        expect(label).to_contain_text("۲۵۱ سند")                      # what goes, said first (AI1)
        expect(label).to_contain_text("۱۵۴ حساب")
        expect(label).to_contain_text("ورود کاربران، مشخصات، تنظیمات")  # what stays
        os.makedirs(ARTIFACTS, exist_ok=True)
        modal.screenshot(path=os.path.join(ARTIFACTS, "companies-reset-books-fa.png"))
        # a wrong name: nothing is sent (AI2)
        page.fill("#ui-prompt-input", "منادی")
        page.click("#ui-prompt-ok")
        page.wait_for_timeout(300)
        assert posted == []
        row.get_by_role("button", name="پاک کردن دفاتر").click()
        page.fill("#ui-prompt-input", NAME)
        page.click("#ui-prompt-ok")
        expect(page.locator("body")).to_contain_text("دفاتر مدرسه منادی حق پاک شد")
        assert posted == [{"confirm": NAME}]
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
