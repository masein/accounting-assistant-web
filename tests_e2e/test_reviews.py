"""The app's own weekly review in the web chat, and each reader's switches (scenarios AL4, AL14), under the strict CSP.

The review is served by the test as the server words it (app/services/proactive_reviews.py); the
switches are the server's own: turning the weekly review off and on again is kept."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

REVIEW = {"kind": "weekly", "choices": ["۸۰٫۰۰۰ ریال عمان برای هدف لپ‌تاپ به پس‌انداز ارزی منتقل کن",
                                        "برای تفریح و رستوران بودجهٔ ماهانه بگذار"],
          "text": "**مرور هفته، ۲۵ مهر تا ۱ آبان ۱۴۰۵**\n"
                  "این هفته ۴۱٫۵۰۰ ریال عمان خرج کردید؛ ۳۷٪ بیشتر از هفته‌های معمولتان (۳۰٫۲۵۰ ریال عمان).\n"
                  "درآمد: ۶۰۰٫۰۰۰ ریال عمان.\nبیشترین خرج: شام تولد، ۱۲٫۵۰۰ ریال عمان (۲۹ مهر ۱۴۰۵).\n"
                  "کجا خرج شد:\n• تفریح و رستوران: ۱۸٫۵۰۰ ریال عمان (۱۶۴٪ بیشتر از معمول)\n"
                  "• خوراک و سوپرمارکت: ۱۵٫۰۰۰ ریال عمان (معمول)\n"
                  "این هفته درآمد داشتید — ۸۰٫۰۰۰ ریال عمان برای «لپ‌تاپ» کنار بگذارید؟\n"
                  "خرج «تفریح و رستوران» ۱۶۴٪ بیشتر از معمول بود — بودجهٔ ماهانه برایش بگذارید؟"}


def test_a_weekly_review_in_the_chat_and_its_buttons(flow_page):
    page, watch = flow_page("e2e_reviews")
    served = []
    page.route("**/ai-accountant/reviews", lambda route: (served.append(1), route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": None, "reviews": [REVIEW] if len(served) == 1 else []}, ensure_ascii=False)))[-1])
    asked = []
    page.route("**/ai-accountant/chat", lambda route: (asked.append(json.loads(route.request.post_data or "{}")), route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": "s-al", "turns": 1, "stop_reason": "end_turn", "tool_calls": [], "proposals": [],
                         "text": "میانگین سه ماه گذشتهٔ تفریح و رستوران ۲۰٫۰۰۰ ریال عمان است."}, ensure_ascii=False)))[-1])
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'ai-accountant'; }")
        page.wait_for_load_state("networkidle")
        bubble = page.locator(".msg-row.assistant .msg").filter(has_text="مرور هفته")
        expect(bubble).to_be_visible()
        expect(bubble.locator("strong").first).to_have_text("مرور هفته، ۲۵ مهر تا ۱ آبان ۱۴۰۵")     # its title, bold
        chips = page.locator(".ai-choices .chip")
        expect(chips).to_have_count(2)
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-weekly-review-fa.png"))
        chips.nth(1).click()                                     # a button asks the assistant, as typed
        expect(page.locator("#ai-acct-messages")).to_contain_text("میانگین سه ماه گذشته")
        assert asked and asked[-1]["message"] == "برای تفریح و رستوران بودجهٔ ماهانه بگذار"
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_the_switches_are_the_readers_own(flow_page):
    page, watch = flow_page("e2e_reviews")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    box = page.locator("#ai-reviews-prefs")
    expect(box).to_be_visible()
    box.locator("summary").click()
    weekly = box.locator("input[data-kind='weekly']")
    expect(weekly).to_be_checked()
    with page.expect_response(lambda r: r.url.endswith("/ai-accountant/review-prefs") and r.request.method == "PUT") as res:
        weekly.uncheck()
    assert res.value.status == 200 and res.value.json()["weekly"] is False
    got = page.evaluate("async () => (await fetch('/ai-accountant/review-prefs')).json()")
    assert got == {"weekly": False, "monthly": True, "suggestions": True, "nudge": True}
    os.makedirs(ARTIFACTS, exist_ok=True)
    box.screenshot(path=os.path.join(ARTIFACTS, "chat-review-switches-en.png"))
    with page.expect_response(lambda r: r.url.endswith("/ai-accountant/review-prefs") and r.request.method == "PUT"):
        weekly.check()
    assert page.evaluate("async () => (await fetch('/ai-accountant/review-prefs')).json()")["weekly"] is True
    assert watch.problems() == [], watch.problems()
