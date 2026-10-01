"""With no AI provider reachable (the browser suite's server has none), the
chat answers in the user's language — not "[error] OpenAI-shape provider
unreachable after 3 attempts"."""
from __future__ import annotations

from tests_e2e.conftest import switch_language


def test_the_chat_says_the_assistant_is_unavailable_in_persian(flow_page):
    page, watch = flow_page("e2e_chat")
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'ai-accountant'; }")
        page.wait_for_load_state("networkidle")
        page.fill("#ai-acct-input", "موجودی نقد چقدر است؟")
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")) as res:
            page.click("#ai-acct-send")
        assert res.value.status == 502 and res.value.headers.get("x-error-code") == "ai_unavailable"
        bubble = page.locator(".msg-row.assistant .msg").last
        bubble.wait_for(timeout=10_000)
        text = bubble.inner_text()
        assert text.startswith("دستیار الان نمی‌تواند پاسخ دهد") and "[error]" not in text, text
    finally:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
