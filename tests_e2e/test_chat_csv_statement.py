"""A CSV bank statement dropped into the chat is imported without the AI: it
went to the model and, with none set up, failed with "the assistant can't
answer" (deep browser test, 2026-10-02, finding #43). The browser suite's
server has no AI provider, so this is that very case."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS

CSV = ("Date,Description,Debit,Credit,Balance\n"
       "2026-09-01,Opening transfer,0,9000000,9000000\n"
       "2026-09-03,Office rent,2500000,0,6500000\n"
       "2026-09-05,Customer payment,0,1200000,7700000\n"
       "2026-09-08,Card purchase,480000,0,7220000\n"
       "2026-09-11,Bank fee,12000,0,7208000\n").encode()


def test_a_csv_statement_in_the_chat_needs_no_ai(flow_page):
    page, watch = flow_page("e2e_chat_csv")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    with page.expect_response(lambda r: "/transactions/attachments" in r.url and r.request.method == "POST"):
        page.set_input_files("#ai-acct-file", files=[{"name": "export.csv", "mimeType": "text/csv", "buffer": CSV}])
    page.fill("#ai-acct-input", "add these")
    with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")) as res:
        page.click("#ai-acct-send")
    assert res.value.status == 200, res.value.text()
    intake = res.value.json()["intake"]
    assert intake["kind"] == "bank_statement" and intake["status"] == "imported" and intake["total_rows"] == 5
    bubble = page.locator(".msg-row.assistant .msg").last
    bubble.wait_for(timeout=10_000)
    assert "5" in bubble.inner_text() and "[error]" not in bubble.inner_text()
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, "chat-csv-statement.png"))
    assert watch.problems() == [], watch.problems()
