"""A bank's statement as a scanned PDF, read page by page after the reply (scenarios AJ1, AJ3, AJ6), on the web.

The owner's own: Day Bank's 22-page statement of pictures (2026-10-20). The real model read all 22
pages, 205 rows, in about three minutes; two amounts it misread (847,522,020 as 47,522,020 and 320,000
as 32,000) were set right from the balances on either side. The server is played by the test: the
reading's progress, then its rows."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

SID = "5d0c3b2a-0000-4000-8000-0000000aj001"
REPORT = {"pages": 22, "pages_read": 22, "failed_pages": [], "beyond": 0, "rows": 205,
          "mended": [{"page": 8, "row": 2, "read": 47522020, "amount": 847522020, "direction": "debit"},
                     {"page": 21, "row": 9, "read": 32000, "amount": 320000, "direction": "debit"}],
          "balance": {"links": 204, "agree": 204, "order": "newest_first", "broken": [], "broken_count": 0}}
INTAKE = {"kind": "bank_statement", "status": "imported", "statement_id": SID, "bank_name": "Day", "bank_label": "دی",
          "file_name": "20261015_133322_receipt.pdf", "total_rows": 205, "from_date": "2026-06-23", "to_date": "2026-09-22",
          "currency": "IRR", "read_report": REPORT,
          "counts": {"matched": 0, "unrecorded": 205, "needs_confirmation": 0, "amount_mismatch": 0, "missing_in_bank": 0},
          "balance": {"statement_closing": 14297545823, "book_balance": 0, "gap": 14297545823}}
RULES = "واریزها بستانکار درآمد شهریه، برداشت‌ها بدهکار مخارج مدرسه، کارمزدها کارمزد بانکی، به تفکیک تاریخ"


def _json(route, body):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))


def test_the_chat_reads_it_page_by_page_then_continues(flow_page):
    page, watch = flow_page("e2e_pdfstmt")
    sent = []

    def chat(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        if len(sent) == 1:
            _json(route, {"session_id": "s-aj", "turns": 1, "stop_reason": "end_turn", "tool_calls": [], "proposals": [],
                          "text": "صورت‌حساب دی را می‌خوانم — ۲۲ صفحه تصویر، صفحه به صفحه؛ کارتش نشان می‌دهد تا کجا خوانده شده."
                                  " وقتی خوانده شد، «ادامه با دستور شما» آن‌ها را اعمال می‌کند.",
                          "intake": {"kind": "bank_statement", "status": "reading", "statement_id": SID, "bank_name": "Day",
                                     "bank_label": "دی", "file_name": "20261015_133322_receipt.pdf",
                                     "pages_total": 22, "pages_read": 0, "rules": True}})
        else:
            _json(route, {"session_id": "s-aj", "turns": 2, "stop_reason": "end_turn", "tool_calls": [], "proposals": [],
                          "text": "کارت آماده است: ۲۰۵ ردیف صورت‌حساب دی، به‌تفکیک تاریخ."})
    polls = []

    def card(route):
        polls.append(1)
        if len(polls) == 1:
            _json(route, {"status": "reading", "pages_total": 22, "pages_read": 9})
        else:
            _json(route, {"status": "parsed", "pages_total": 22, "pages_read": 22, "intake": INTAKE,
                          "text": "صورت‌حساب دی را خواندم: ۲۰۵ ردیف. ۲۲ صفحه، ۲۰۵ ردیف خوانده شد؛"
                                  " مانده‌ها از هر ردیف به ردیف بعد جور است."})
    page.route("**/ai-accountant/chat", chat)
    page.route(f"**/ai-accountant/statements/{SID}/card", card)
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    try:
        switch_language(page, "fa")
        page.fill("#ai-acct-input", RULES)
        page.click("#ai-acct-send")
        reading = page.locator(".ai-reading-card")
        expect(reading).to_be_visible()
        expect(reading.locator(".ai-reading-title")).to_have_text("صورت‌حساب بانکی — دی")   # not «imported» yet
        expect(reading.locator(".ai-reading-line")).to_contain_text("۹ از ۲۲")         # how far, from the server
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-pdf-reading-fa.png"))

        go = page.locator(".ai-reading-continue")
        expect(go).to_be_visible(timeout=15_000)                                         # read: its card, then the button
        expect(go).to_have_text("ادامه با دستور شما")
        report = page.locator(".ai-reading-report")
        expect(report).to_have_class("ai-reading-report is-ok")
        expect(report).to_contain_text("صفحهٔ ۸، ردیف ۲: ۴۷٬۵۲۲٬۰۲۰ ← ۸۴۷٬۵۲۲٬۰۲۰")
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-pdf-read-fa.png"))

        go.click()                                                                       # the same words again
        expect(page.locator("#ai-acct-messages")).to_contain_text("کارت آماده است: ۲۰۵ ردیف")
        assert len(sent) == 2 and sent[1]["message"] == RULES
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_the_bank_page_follows_the_reading(flow_page):
    page, watch = flow_page("e2e_pdfstmt")
    asked = []

    def reading(route):
        asked.append(1)
        if len(asked) == 1:
            _json(route, {"id": SID, "status": "reading", "total_rows": 0, "bank_name": "Day",
                          "pages_total": 22, "pages_read": 14, "read_report": None})
        else:
            _json(route, {"id": SID, "status": "parsed", "total_rows": 205, "bank_name": "Day",
                          "pages_total": 22, "pages_read": 22, "read_report": REPORT})
    rows = [{"id": f"r{i}", "row_index": i, "tx_date": f"2026-09-{23 - i:02d}", "description": d, "debit": dr, "credit": cr,
             "balance": bal, "confidence": conf, "recon_status": "unmatched", "category": None,
             "suggested_account_code": None, "user_approved": False}
            for i, (d, dr, cr, bal, conf) in enumerate([
                ("واریز شهریه", 0, 200000000, 14297545823, 0.85),
                ("واریز شهریه", 0, 400000000, 14097545823, 0.85),
                ("خرید توسط کارت", 847522020, 0, 13697545823, 0.6),                    # set right from its balances
                ("برداشت کارمزد کارمزد ساتنا", 320000, 0, 14545067843, 0.6),
                ("واریز شهریه", 0, 350000000, 14545387843, 0.85)], start=1)]
    detail = {"id": SID, "bank_name": "Day", "account_number": "0105222028000", "source_type": "ocr_pdf",
              "source_filename": "20261015_133322_receipt.pdf", "currency": "IRR", "from_date": "2026-06-23",
              "to_date": "2026-09-22", "status": "parsed", "total_rows": 205, "matched_rows": 0, "new_rows": 205,
              "pages_total": 22, "pages_read": 22, "read_report": REPORT, "rows": rows}
    page.route("**/brain/bank-statements/upload*", lambda route: _json(route, {
        "id": SID, "status": "reading", "bank_name": "Day", "source_type": "ocr_pdf", "total_rows": 0,
        "pages_total": 22, "currency": "IRR"}))
    page.route(f"**/brain/bank-statements/{SID}/reading", reading)
    page.route(f"**/brain/bank-statements/{SID}", lambda route: _json(route, detail))
    page.evaluate("() => { location.hash = 'bank-statements'; }")
    page.wait_for_load_state("networkidle")
    try:
        switch_language(page, "fa")
        page.set_input_files("#bs-file-input", files=[{"name": "20261015_133322_receipt.pdf", "mimeType": "application/pdf",
                                                       "buffer": b"%PDF-1.4\n%%EOF\n"}])
        page.click("#bs-upload-btn")
        status = page.locator("#bs-upload-status")
        expect(status).to_contain_text("۱۴")                                              # 14 of 22 pages
        expect(status).to_contain_text("۲۲")
        os.makedirs(ARTIFACTS, exist_ok=True)
        status.screenshot(path=os.path.join(ARTIFACTS, "bank-pdf-reading-fa.png"))

        expect(status).to_contain_text("۲۰۵", timeout=15_000)
        expect(status).to_contain_text("صفحهٔ ۲۱، ردیف ۹: ۳۲٬۰۰۰ ← ۳۲۰٬۰۰۰")
        expect(page.locator("#bs-rows-body tr")).to_have_count(5)
        page.screenshot(path=os.path.join(ARTIFACTS, "bank-pdf-read-fa.png"), full_page=True)
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_aj10_a_ten_megabyte_scan_goes_in_the_chat(flow_page):
    """AJ10 (the owner, 2026-10-21): the same 22-page scan is 10.4 MB, and the chat refused it at 8 MB while the
    Bank Statements page took it. Up to 20 MB now; over that, it is said before anything is sent."""
    page, watch = flow_page("e2e_pdfstmt")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    try:
        switch_language(page, "fa")
        scan = b"%PDF-1.4\n" + b"0" * 10_400_000
        with page.expect_response(lambda r: r.url.endswith("/transactions/attachments") and r.request.method == "POST") as res:
            page.set_input_files("#ai-acct-file", files=[{"name": "20261015_133322_receipt.pdf", "mimeType": "application/pdf",
                                                          "buffer": scan}])
        assert res.value.status == 201, res.value.text()[:200]
        expect(page.locator("#ai-acct-attachments .ai-attachment")).to_contain_text("20261015_133322_receipt.pdf")
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-attachments").screenshot(path=os.path.join(ARTIFACTS, "chat-pdf-10mb-fa.png"))
        sent = []
        page.on("request", lambda r: sent.append(r.url) if r.url.endswith("/transactions/attachments") else None)
        page.set_input_files("#ai-acct-file", files=[{"name": "too-big.pdf", "mimeType": "application/pdf",
                                                      "buffer": b"%PDF-1.4\n" + b"0" * (21 * 1024 * 1024)}])
        expect(page.locator("#alert")).to_contain_text("حداکثر ۲۰ مگابایت")
        assert sent == []                                                               # refused before it is sent
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
