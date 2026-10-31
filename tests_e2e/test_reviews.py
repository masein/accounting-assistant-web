"""The app's own weekly review in the web chat, and each reader's switches (scenarios AL4, AL14), under the strict CSP.

The review is served by the test as the server words it (app/services/proactive_reviews.py); the
switches are the server's own: turning the weekly review off and on again is kept."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

# the cards as the server draws them (app/services/proactive_reviews.py render(...)["card"])
CARDS = json.loads((Path(__file__).parent / "fixtures" / "review_cards.json").read_text(encoding="utf-8"))


def _review(card, text="—"):
    return {"kind": card["kind"], "text": text, "choices": [i["ask"] for i in card["ideas"]], "card": card}


def test_a_weekly_review_in_the_chat_and_its_buttons(flow_page):
    """AL17: the week as a card — its emoji, the figure counted up, the change, five bars and the usual
    line, the categories as bars, two ideas with a button each; then the month with its ring."""
    page, watch = flow_page("e2e_reviews")
    served = []
    reviews = [_review(CARDS["week_fa"]), _review(CARDS["month_fa"])]
    page.route("**/ai-accountant/reviews", lambda route: (served.append(1), route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": None, "reviews": reviews if len(served) == 1 else []}, ensure_ascii=False)))[-1])
    asked = []
    page.route("**/ai-accountant/chat", lambda route: (asked.append(json.loads(route.request.post_data or "{}")), route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": "s-al", "turns": 1, "stop_reason": "end_turn", "tool_calls": [], "proposals": [],
                         "text": "کارت آماده است: بودجهٔ ماهانهٔ ۳۰٫۰۰۰ ریال عمان برای «تفریح و رستوران»."},
                        ensure_ascii=False)))[-1])
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'ai-accountant'; }")
        page.wait_for_load_state("networkidle")
        week = page.locator(".ai-review-card.mood-watch")
        expect(week).to_be_visible()
        expect(week.locator(".ai-review-emoji")).to_have_text("👀")
        expect(week.locator(".ai-review-title")).to_have_text("مرور هفته، ۲۵ مهر تا ۱ آبان ۱۴۰۵")
        expect(week.locator(".ai-review-figure")).to_have_text("۴۱٫۵۰۰")            # counted up to the books' figure
        expect(week.locator(".ai-review-delta")).to_contain_text("۳۷٪ بیشتر از معمول")
        expect(week.locator(".ai-review-col")).to_have_count(5)
        expect(week.locator(".ai-review-col-label.is-current")).to_have_text("این هفته")
        expect(week.locator(".ai-review-usual")).to_have_count(1)
        expect(week.locator(".ai-review-cat")).to_have_count(3)
        expect(week.locator(".ai-review-idea")).to_have_count(2)
        month = page.locator(".ai-review-card.mood-good")
        expect(month.locator(".ai-review-emoji")).to_have_text("🏆")
        expect(month.locator(".ai-review-ring-pct")).to_have_text("۳۲٪ از درآمد")
        os.makedirs(ARTIFACTS, exist_ok=True)
        for card, name in ((week, "review-week-fa.png"), (month, "review-month-fa.png")):
            card.scroll_into_view_if_needed()
            page.wait_for_timeout(1300)                                           # the bars grown, the ring filled
            card.screenshot(path=os.path.join(ARTIFACTS, name))
        week.locator(".ai-review-ask").nth(1).click()                             # an idea's button asks, as typed
        expect(page.locator("#ai-acct-messages")).to_contain_text("کارت آماده است: بودجهٔ ماهانهٔ")
        assert asked and asked[-1]["message"] == CARDS["week_fa"]["ideas"][1]["ask"]
        expect(week.locator(".ai-review-ask").first).to_be_disabled()
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_a_week_in_two_currencies_is_a_card_for_each(flow_page):
    """AP1, AP6: rials and Omani rials, never added together — the rials' card first, then the Omani rials'."""
    page, watch = flow_page("e2e_reviews")
    cards = CARDS["week_two_fa"]
    page.route("**/ai-accountant/reviews", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": None, "reviews": [{**_review(cards[0]), "cards": cards}]}, ensure_ascii=False)))
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'ai-accountant'; }")
        page.wait_for_load_state("networkidle")
        drawn = page.locator(".ai-review-card")
        expect(drawn).to_have_count(2)
        expect(drawn.nth(0).locator(".ai-review-title")).to_contain_text("— ریال ایران")
        expect(drawn.nth(1).locator(".ai-review-title")).to_contain_text("— ریال عمان")
        expect(drawn.nth(1).locator(".ai-review-figure")).to_have_text("۴۱٫۵۰۰")
        expect(drawn.nth(1).locator(".ai-review-cat")).to_have_count(3)
        os.makedirs(ARTIFACTS, exist_ok=True)
        for i, name in ((0, "review-two-rials-fa.png"), (1, "review-two-omani-fa.png")):
            drawn.nth(i).scroll_into_view_if_needed()
            page.wait_for_timeout(1300)
            drawn.nth(i).screenshot(path=os.path.join(ARTIFACTS, name))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_an_english_month_on_a_phone_sized_screen(flow_page):
    page, watch = flow_page("e2e_reviews")
    page.set_viewport_size({"width": 400, "height": 900})
    page.route("**/ai-accountant/reviews", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": None, "reviews": [_review(CARDS["month_en"])]}, ensure_ascii=False)))
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    card = page.locator(".ai-review-card.mood-good")
    expect(card).to_be_visible()
    expect(card.locator(".ai-review-headline .ai-review-label")).to_have_text("Spent in Mehr 1405")
    page.wait_for_timeout(1300)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")   # no sideways scroll
    os.makedirs(ARTIFACTS, exist_ok=True)
    card.screenshot(path=os.path.join(ARTIFACTS, "review-month-en-narrow.png"))
    assert watch.problems() == [], watch.problems()


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


def test_can_i_afford_it_as_a_card(flow_page):
    """AM9: «می‌خوام یه ماشین ۳۰ میلیون تومنی بخرم، می‌تونم؟» — save first: the price counted up, now and after
    against the cushion, the ring of what the free money covers, and the goal to make."""
    page, watch = flow_page("e2e_reviews")
    page.route("**/ai-accountant/reviews", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"session_id": None, "reviews": []})))
    page.route("**/ai-accountant/chat", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"session_id": "s-am", "turns": 2, "stop_reason": "end_turn", "tool_calls": [], "proposals": [],
                         "text": "فعلاً نه به‌صورت نقد؛ ماهی حدود ۱۵ میلیون ریال کنار بگذارید.",
                         "review_cards": [CARDS["afford_fa"]]}, ensure_ascii=False)))
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'ai-accountant'; }")
        page.wait_for_load_state("networkidle")
        page.fill("#ai-acct-input", "می‌خوام یه ماشین ۳۰ میلیون تومنی بخرم، می‌تونم؟")
        page.click("#ai-acct-send")
        card = page.locator(".ai-review-card.mood-watch")
        expect(card).to_be_visible()
        expect(card.locator(".ai-review-emoji")).to_have_text("🐷")
        expect(card.locator(".ai-review-badge")).to_have_text("می‌توانم بخرم؟")
        expect(card.locator(".ai-review-headline .ai-review-label")).to_have_text("ماهی ۱۵٬۰۰۰٬۰۰۰ ریال ایران کنار بگذارید، حدود ۱۴ ماه")
        expect(card.locator(".ai-review-col")).to_have_count(2)
        expect(card.locator(".ai-review-ring-pct")).to_have_text("۳۲٪ از قیمت")
        expect(card.locator(".ai-review-idea")).to_have_count(2)
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(1300)
        os.makedirs(ARTIFACTS, exist_ok=True)
        card.screenshot(path=os.path.join(ARTIFACTS, "afford-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
