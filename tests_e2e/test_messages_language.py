"""A save's message is in the user's language: in Persian, adding a party says
"طرف حساب افزوده شد." — not "Entity added." (the scripts' showAlert messages
were English until 2026-10)."""
from __future__ import annotations

import uuid


def _alert(page) -> str:
    page.wait_for_selector("#alert span", timeout=5_000)
    return page.inner_text("#alert span")


def test_the_entity_messages_are_persian(flow_page):
    page, watch = flow_page("e2e_messages")
    try:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'fa';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
        page.wait_for_load_state("networkidle")
        page.click('.nav-btn[data-page="entities"]')
        page.fill("#entity-name", "")
        page.click("#entity-add")
        assert _alert(page) == "نام طرف حساب را وارد کنید."
        page.fill("#entity-name", f"شرکت پیام {uuid.uuid4().hex[:6]}")
        with page.expect_response(lambda r: r.url.endswith("/entities") and r.request.method == "POST") as res:
            page.click("#entity-add")
        assert res.value.status in (200, 201)
        page.wait_for_function("() => document.querySelector('#alert span')?.textContent === 'طرف حساب افزوده شد.'", timeout=5_000)
        assert watch.problems() == [], watch.problems()
    finally:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
