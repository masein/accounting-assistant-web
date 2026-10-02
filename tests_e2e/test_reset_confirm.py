"""Settings' wipe asks for the company's name typed back: a wrong name deletes
nothing and says so in the user's language (no reset request is even sent)."""
from __future__ import annotations

from tests_e2e.conftest import switch_language


def test_a_wrong_name_wipes_nothing(app_page):
    page, watch = app_page
    sent = []
    page.on("request", lambda r: sent.append(r.url) if "/admin/reset-db" in r.url else None)
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'settings'; }")
        page.wait_for_load_state("networkidle")
        page.evaluate("() => document.querySelectorAll('.card[data-page=\"settings\"] details').forEach(d => { d.open = true; })")
        page.locator("#reset-empty-btn").scroll_into_view_if_needed()
        page.click("#reset-empty-btn")
        assert page.locator("#ui-prompt-modal").is_visible()
        assert "«" in page.inner_text("#ui-prompt-label")
        page.fill("#ui-prompt-input", "not the name")
        page.click("#ui-prompt-ok")
        page.wait_for_timeout(500)
        assert sent == []
        assert "چیزی پاک نشد" in page.inner_text("#alert")
    finally:
        try:
            switch_language(page, "en")
        except Exception:
            pass
