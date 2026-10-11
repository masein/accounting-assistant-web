"""A conversation opened again shows what was sent (scenario N39): a receipt
sent with no words came back as "Attached document OCR (attachment_id=…" —
the prompt written for the model — shown as the user's own message. The
browser suite's server has no AI, so the turn fails after the message is
kept, which is enough: the reload must show the receipt's chip."""
from __future__ import annotations

import base64
import os

from tests_e2e.conftest import ARTIFACTS

# a 1×1 PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def test_a_receipt_sent_alone_comes_back_as_its_chip_not_the_models_prompt(flow_page):
    page, watch = flow_page("e2e_chat_history")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    with page.expect_response(lambda r: "/transactions/attachments" in r.url and r.request.method == "POST"):
        page.set_input_files("#ai-acct-file", files=[{"name": "bakery-receipt.png", "mimeType": "image/png", "buffer": PNG}])
    with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")):
        page.click("#ai-acct-send")                       # no words: the receipt alone; no AI here, so it fails
    page.reload()
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    mine = page.locator(".msg-row.user .msg").last
    mine.wait_for(timeout=10_000)
    said = mine.inner_text()
    assert "bakery-receipt.png" in said, said
    assert "Attached document" not in said and "attachment_id" not in said, said
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, "chat-history-files.png"))
    # the failed answer is expected here (no AI); nothing else may go wrong
    assert [p for p in watch.problems() if "/ai-accountant/chat" not in p] == [], watch.problems()
