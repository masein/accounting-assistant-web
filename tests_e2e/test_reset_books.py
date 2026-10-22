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


def test_the_ai_column_says_which_budget_and_sets_each_users(flow_page):
    """AV3, AV4: «2.1M / 10M *» with the legend under the table; a company whose busiest user is near a tighter
    per-user budget shows a second line; the budget dialog asks for each user's on a company, not on a person's."""
    page, watch = flow_page("e2e_wipe")
    person = {**COMPANY, "id": "c0ffee00-0000-4000-8000-000000000b22", "name": "masein", "kind": "personal",
              "locale": "default", "login_username": "masein"}
    usage = [
        {"company_id": COMPANY["id"], "name": NAME, "tokens_24h": 2_400_000, "budget": 10_000_000,
         "budget_is_default": True, "personal": False, "user_budget": 2_000_000, "user_budget_is_default": True,
         "top_user_tokens_24h": 2_100_000, "tokens_30d": 0, "cost_usd_30d": 0},
        {"company_id": person["id"], "name": "masein", "tokens_24h": 2_100_000, "budget": 10_000_000,
         "budget_is_default": False, "personal": True, "user_budget": 10_000_000, "user_budget_is_default": True,
         "top_user_tokens_24h": 2_100_000, "tokens_30d": 0, "cost_usd_30d": 0},
    ]
    sent = []
    _serve(page, "**/admin/companies", [COMPANY, person], method="GET")
    _serve(page, "**/admin/companies/ai-usage", usage)
    _serve(page, "**/admin/companies/ai-review", [])
    page.route("**/admin/companies/*/ai-budget", lambda route: (
        sent.append(route.request.post_data_json),
        route.fulfill(status=200, content_type="application/json", body=json.dumps(usage[0]))))
    page.evaluate("() => { isSuperadmin = true; location.hash = 'companies'; }")
    page.wait_for_load_state("networkidle")
    page.evaluate("() => loadCompanies()")
    company_row = page.locator("#companies-tbody tr").filter(has_text=NAME)
    person_row = page.locator("#companies-tbody tr").filter(has_text="masein")
    expect(company_row).to_contain_text("per user 2.1M / 2.0M")        # the tighter one, said
    expect(person_row).to_contain_text("2.1M / 10M *")
    expect(person_row).not_to_contain_text("per user")                 # one person: one budget
    expect(page.locator("[data-i18n='companiesAiLegend']")).to_contain_text("* set for this company")
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator("#companies-table").screenshot(path=os.path.join(ARTIFACTS, "companies-ai-budgets-en.png"))
    # a person's books: one question
    person_row.get_by_role("button", name="AI budget").click()
    page.fill("#ui-prompt-input", "20000000")
    page.click("#ui-prompt-ok")
    page.wait_for_timeout(400)
    assert sent[-1] == {"daily_tokens": 20_000_000}
    # a company: then each user's
    company_row.get_by_role("button", name="AI budget").click()
    page.fill("#ui-prompt-input", "")
    page.click("#ui-prompt-ok")
    expect(page.locator("#ui-prompt-title")).to_have_text("AI budget per user")
    page.fill("#ui-prompt-input", "5000000")
    page.click("#ui-prompt-ok")
    page.wait_for_timeout(400)
    assert sent[-1] == {"daily_tokens": None, "user_daily_tokens": 5_000_000}
    assert watch.problems() == [], watch.problems()
