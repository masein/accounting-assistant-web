"""The bell's alerts read in the page's language: an overdue invoice said
"Invoice … overdue" in Persian too."""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS

POST = r"""async (body) => { const r = await fetch('/invoices', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body) }); return [r.status, await r.text()]; }"""


def test_the_bell_speaks_persian(flow_page):
    page, watch = flow_page("e2e_persian")
    number = f"BELL-{uuid.uuid4().hex[:5]}"
    try:
        status, body = page.evaluate(POST, {"number": number, "kind": "sales", "status": "issued", "amount": 1000,
                                            "issue_date": (date.today() - timedelta(days=40)).isoformat(),
                                            "due_date": (date.today() - timedelta(days=10)).isoformat()})
        assert status == 201, body
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'fa';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
        page.wait_for_load_state("networkidle")
        with page.expect_response(lambda r: r.url.endswith("/notifications/feed")):
            page.click("#notify-bell-btn")
        item = page.locator("#notify-list .notify-item", has_text=number).first
        item.wait_for(timeout=15_000)
        assert "سررسید گذشته" in item.inner_text(), item.inner_text()
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "bell-language.png"), full_page=True)
        raise
    finally:
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")
