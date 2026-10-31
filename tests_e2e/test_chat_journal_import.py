"""A school's journals in the chat, its misread vouchers, and its own codes, on the web (scenarios AH1–AH7).

The server's side is tests/test_chat_journal_import.py; here its replies are served by the test, as
it now sends them for منادی حق's two files: a card per file in the words said above it, «وارد کردن»
importing whole vouchers, accounts to match sent to the journal page, an earlier import's misread
vouchers listed to remove on the user's word, and the chart showing each account's old code."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

SAID_1 = "\u2068manadi-1404.xlsx\u2069 با ۱۹۹ سند از ۲۹ فروردین ۱۴۰۴ تا ۲۹ اسفند ۱۴۰۴"
SAID_2 = "\u2068manadi-1405.xlsx\u2069 با ۵۳ سند از ۶ فروردین ۱۴۰۵ تا ۳۱ خرداد ۱۴۰۵"
INTAKE = {"kind": "journals", "repairs": 6, "calendar": "jalali", "files": [
    {"file_name": "manadi-1404.xlsx", "token": "a" * 32, "voucher_count": 199, "from": "2025-04-18", "to": "2026-03-20",
     "counts": {"ready": 199}, "ready": 199, "unplaced_accounts": 0, "new_accounts": 17, "stray_years": 0,
     "said": SAID_1, "notes": ["۱۹۹ سند آمادهٔ ورود", "۱۷ حساب تازه در کدینگ ساخته می‌شود",
                               "در فایل، کد ۱۳۰۵ به ۳ حساب داده شده است: سهم هزینه مشترک با فرهنگ نو، قبوض، کارمزد بانکی",
                               "ورود قبلی ۶ سند را نادرست ثبت کرده؛ «انتقال اطلاعات» فهرستشان را برای حذف نشان می‌دهد"]},
    {"file_name": "manadi-1405.xlsx", "token": "b" * 32, "voucher_count": 53, "from": "2026-03-26", "to": "2026-06-21",
     "counts": {"ready": 52, "opening_repeat": 1}, "ready": 52, "unplaced_accounts": 2, "new_accounts": 0,
     "stray_years": 0, "said": SAID_2, "notes": ["۵۲ سند آمادهٔ ورود", "۲ حساب معادل‌سازی‌نشده", "کنار گذاشته می‌شود: ۱ سند افتتاحیهٔ تکراری"]},
]}
REPLY = {"session_id": "s-ah", "turns": 1, "stop_reason": "end_turn", "tool_calls": [], "proposals": [], "intake": INTAKE,
         "text": "به نظر فهرست سندهاست: " + SAID_1 + "؛ " + SAID_2 + "؛ ۲ حساب معادل‌سازی‌نشده."}
APPLIED = {"posted": 199, "skipped": {}, "said": "۱۹۹ سند از \u2068manadi-1404.xlsx\u2069 وارد دفاتر شد، از ۲۹ فروردین ۱۴۰۴ تا ۲۹ اسفند ۱۴۰۴."}
REPAIRS = {"vouchers": [{"id": f"00000000-0000-4000-8000-00000000000{i}", "reference": ref, "date": "2027-01-30",
                         "debit": 0, "credit": 200_000_000, "lines": 1, "currency": "IRR"}
                        for i, ref in enumerate(["V1404/01/29", "V1404/01/31", "V1404/02/03", "V1404/02/06",
                                                 "V1404/02/09", "V1404/02/13"])],
           "headings": [{"name": "مخارج مدرسه", "keep": "6132", "merge": [{"code": "6211", "accounts": ["621101"]}]}]}
TREE = {"accounts": [{"id": "g61", "code": "61", "name": "هزینه‌های عملیاتی", "level": "GROUP", "parent_id": None,
                      "detail_type": None, "is_active": True, "protected": True, "source_codes": [], "balance": 0,
                      "total": 5_000_000, "children": [
                          {"id": "h", "code": "6132", "name": "مخارج مدرسه", "level": "GENERAL", "parent_id": "g61",
                           "detail_type": None, "is_active": True, "protected": False, "source_codes": ["1300"],
                           "balance": 0, "total": 5_000_000, "children": [
                               {"id": "s", "code": "613202", "name": "ملزومات اداری", "level": "SUB", "parent_id": "h",
                                "detail_type": None, "is_active": True, "protected": False, "source_codes": ["1302"],
                                "balance": 5_000_000, "total": 5_000_000, "children": []}]}]}]}


def _serve(page, url, body):
    page.route(url, lambda route: route.fulfill(status=200, content_type="application/json",
                                                body=json.dumps(body, ensure_ascii=False)))


def test_two_years_dropped_each_its_card_imported_whole(flow_page):
    page, watch = flow_page("e2e_journals")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    try:
        switch_language(page, "fa")
        _serve(page, "**/ai-accountant/chat", REPLY)
        _serve(page, "**/migration/journals/apply", APPLIED)
        page.fill("#ai-acct-input", "این فایل های اکسل دو سال گذشته مدرسه هست")
        page.click("#ai-acct-send")
        cards = page.locator(".ai-journal-card")
        expect(cards).to_have_count(2)
        expect(cards.nth(0).locator(".ai-journal-title")).to_have_text("به نظر فهرست سندهاست")
        expect(cards.nth(0).locator(".ai-journal-said")).to_have_text(SAID_1)
        expect(cards.nth(1).get_by_role("button", name="معادل‌سازی حساب‌ها")).to_be_visible()     # accounts to match first
        expect(cards.nth(0).get_by_role("button", name="انتقال اطلاعات")).to_be_visible()  # the misread six
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-journals-fa.png"))
        cards.nth(0).get_by_role("button", name="وارد کردن ۱۹۹ سند").click()
        expect(page.locator(".msg-row.assistant .msg").last).to_contain_text("وارد دفاتر شد")
        expect(cards.nth(0).locator(".ai-journal-actions button").first).to_contain_text("✓")
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-journals-imported-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_misread_vouchers_listed_and_old_codes_on_the_chart(flow_page):
    page, watch = flow_page("e2e_journals")
    try:
        switch_language(page, "fa")
        _serve(page, "**/migration/journals/repairs", REPAIRS)
        page.evaluate("() => { location.hash = 'migration'; }")
        page.wait_for_load_state("networkidle")
        panel = page.locator(".ji-repairs")
        expect(panel).to_be_visible()
        expect(panel.locator(".ji-repairs-list li")).to_have_count(6)
        expect(panel).to_contain_text("«مخارج مدرسه» دو بار ساخته شده")
        expect(panel.get_by_role("button", name="حذف و ادغام")).to_be_visible()
        os.makedirs(ARTIFACTS, exist_ok=True)
        panel.screenshot(path=os.path.join(ARTIFACTS, "migration-repairs-fa.png"))

        _serve(page, "**/accounts/tree*", TREE)
        page.evaluate("() => { location.hash = 'accounts'; }")
        page.wait_for_load_state("networkidle")
        page.fill("#coa-filter", "۱۳۰۲")
        rows = page.locator("#coa-tree tbody tr")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("ملزومات اداری")
        expect(rows.first.locator(".coa-old-code")).to_have_text("(کد قبلی ۱۳۰۲)")
        page.fill("#coa-filter", "")
        page.locator("#coa-tree").screenshot(path=os.path.join(ARTIFACTS, "chart-old-codes-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
