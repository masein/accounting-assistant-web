"""Reply, copy and share on the web chat (scenarios AC1–AC5), under the strict CSP.

Pointing at a message shows Reply, Copy and Share beside its name and time.
Reply puts the message above the composer; the next message goes with it and
shows it above its words. A card can be replied to as well: the reply names
the card, so a correction replaces that card. The chat's replies are served
by the test."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS

ANSWER = "در کیف پول سبز ۷۷٫۵۰۰ ریال عمان دارید."
TOKEN = "7f1c2a9e-0000-4000-8000-00000000c0de"
CARD = {"confirmation_token": TOKEN, "tool_name": "propose_money_place",
        "summary": "کیف پول سبز: 77.500 OMR (Omani rial), its starting balance, against opening net worth.",
        "preview": {"description": "کیف پول سبز — موجودی اولیه"}, "expires_at": None}
FIRST = {"session_id": "s-replies", "text": ANSWER, "proposals": [CARD], "turns": 2, "stop_reason": "end_turn", "tool_calls": []}
LATER = {"session_id": "s-replies", "text": "حدود ۲۰۰ دلار.", "proposals": [], "turns": 1, "tool_calls": []}


def test_reply_copy_and_share_a_message_and_a_card(flow_page):
    page, watch = flow_page("e2e_replies")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    sent: list[dict] = []

    def answer(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        body = FIRST if len(sent) == 1 else LATER
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
    page.route("**/ai-accountant/chat", answer)
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    page.fill("#ai-acct-input", "پول من کجاست؟")
    page.click("#ai-acct-send")
    said = page.locator("#ai-acct-messages .msg-row.assistant").last
    expect(said).to_contain_text("ریال عمان دارید")

    # AC1: copy the words as they read
    said.hover()
    said.get_by_role("button", name="Copy").click()
    expect(said.locator(".msg-actions")).to_have_attribute("data-note", "Copied")
    assert page.evaluate("() => navigator.clipboard.readText()") == ANSWER

    # AC3: no share sheet in this browser: Telegram, WhatsApp, e-mail
    said.get_by_role("button", name="Share").click()
    links = page.locator(".msg-share-menu a")
    expect(links).to_have_count(3)
    assert links.nth(0).get_attribute("href").startswith("https://t.me/share/url?url=")
    assert links.nth(1).get_attribute("href").startswith("https://wa.me/?text=")
    assert links.nth(2).get_attribute("href").startswith("mailto:?body=")
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator(".ai-chat").screenshot(path=os.path.join(ARTIFACTS, "chat-share-menu.png"))
    page.keyboard.press("Escape")
    page.locator("#ai-acct-input").click()
    expect(links).to_have_count(0)

    # AC4: reply to the answer
    said.hover()
    said.get_by_role("button", name="Reply").click()
    strip = page.locator(".ai-reply-strip")
    expect(strip).to_be_visible()
    expect(strip).to_contain_text("Replying to the assistant")
    expect(strip).to_contain_text("ریال عمان دارید")
    page.fill("#ai-acct-input", "و به دلار؟")
    page.locator(".ai-chat").screenshot(path=os.path.join(ARTIFACTS, "chat-reply-strip.png"))
    page.click("#ai-acct-send")
    expect(page.locator("#ai-acct-messages")).to_contain_text("حدود ۲۰۰ دلار")
    expect(strip).to_be_hidden()
    mine = page.locator("#ai-acct-messages .msg-row.user").last
    expect(mine.locator(".msg-quote")).to_contain_text("ریال عمان دارید")
    assert sent[1]["message"] == "و به دلار؟"
    assert sent[1]["reply_to"] == {"role": "assistant", "text": ANSWER, "token": None}
    assert sent[1]["session_id"] == "s-replies"

    # AC5: reply to the card: it is named
    card = page.locator(f'.ai-card[data-token="{TOKEN}"]')
    card.hover()
    card.get_by_role("button", name="Reply").click()
    expect(strip).to_contain_text("کیف پول سبز — موجودی اولیه")
    page.fill("#ai-acct-input", "مبلغش رو ۸۰ کن")
    page.click("#ai-acct-send")
    expect(page.locator("#ai-acct-messages .msg-row.user")).to_have_count(3)
    assert sent[2]["reply_to"] == {"role": "assistant", "text": "کیف پول سبز — موجودی اولیه", "token": TOKEN}
    page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-replies.png"))
    # the next message answers nothing
    page.fill("#ai-acct-input", "ممنون")
    page.click("#ai-acct-send")
    expect(page.locator("#ai-acct-messages .msg-row.user")).to_have_count(4)
    assert sent[3]["reply_to"] is None
    assert watch.problems() == [], watch.problems()


SWIPE = """([dx]) => {
  const el = [...document.querySelectorAll('#ai-acct-messages .msg-row.assistant .msg')].pop();
  const r = el.getBoundingClientRect(), x = r.left + r.width * 0.7, y = r.top + r.height / 2;
  const at = (type, cx) => el.dispatchEvent(new PointerEvent(type, {pointerType: 'touch', pointerId: 7, isPrimary: true,
                                                                   clientX: cx, clientY: y, bubbles: true}));
  at('pointerdown', x);
  for (let i = 1; i <= 8; i++) at('pointermove', x - dx * i / 8);
  const moved = el.style.transform;
  at('pointerup', x - dx);
  return moved;
}"""


def test_swipe_left_replies_on_a_touch_screen(flow_page):
    """Scenario AC11: the answer follows the finger; past the line, letting go replies; a short swipe does nothing."""
    page, watch = flow_page("e2e_replies")
    page.route("**/ai-accountant/chat", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(FIRST, ensure_ascii=False)))
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    page.fill("#ai-acct-input", "پول من کجاست؟")
    page.click("#ai-acct-send")
    expect(page.locator("#ai-acct-messages .msg-row.assistant").last).to_contain_text("ریال عمان دارید")
    strip = page.locator(".ai-reply-strip")
    assert page.evaluate(SWIPE, [30]).startswith("translateX(-")         # it follows the finger
    expect(strip).to_be_hidden()                                          # short of the line: nothing
    page.evaluate(SWIPE, [120])
    expect(strip).to_be_visible()
    expect(strip).to_contain_text("ریال عمان دارید")
    assert watch.problems() == [], watch.problems()
