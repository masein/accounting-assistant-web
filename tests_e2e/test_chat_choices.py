"""A reply's answers to tap, under its words (scenario AB1), on the web chat under the strict CSP.

The assistant asks «count the books in Omani rials, or keep this in its own
currency?» with ask_with_choices; the page draws the answers as buttons, and a
tap sends the answer as if typed. The chat's replies are served by the test."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS

ASK = {"session_id": "s-choices", "text": "دفاتر شما به ریال ایران است. ارز دفاتر ریال عمان شود، یا این پول به ریال عمان بماند؟",
       "proposals": [], "turns": 2, "stop_reason": "end_turn",
       "tool_calls": [{"name": "ask_with_choices", "input": {}, "result": {"options": ["دفاتر به ریال عمان", "ریال ایران بماند"]}}]}
DONE = {"session_id": "s-choices", "text": "باشد، ریال ایران می‌ماند.", "proposals": [], "turns": 1, "tool_calls": []}


def test_a_question_offers_its_answers_and_a_tap_sends_one(flow_page):
    page, watch = flow_page("e2e_choices")
    sent: list[dict] = []

    def answer(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        body = ASK if len(sent) == 1 else DONE
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
    page.route("**/ai-accountant/chat", answer)
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    page.fill("#ai-acct-input", "۷۷.۵ ریال عمان توی کیف پول سبز داریم")
    page.click("#ai-acct-send")
    chips = page.locator("#ai-acct-messages .ai-choices .chip")
    expect(chips).to_have_count(2)
    expect(chips.nth(1)).to_have_text("ریال ایران بماند")
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-choices.png"))
    chips.nth(1).click()
    expect(page.locator("#ai-acct-messages .ai-choices")).to_have_count(0)        # the row goes once one is picked
    expect(page.locator("#ai-acct-messages")).to_contain_text("ریال ایران می‌ماند")
    assert [m["message"] for m in sent] == ["۷۷.۵ ریال عمان توی کیف پول سبز داریم", "ریال ایران بماند"]
    assert sent[1]["session_id"] == "s-choices"
    assert watch.problems() == [], watch.problems()
