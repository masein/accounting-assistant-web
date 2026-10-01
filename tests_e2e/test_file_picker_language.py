"""A file field is in the user's language. Chrome writes "Choose Files" /
"No file chosen" on <input type=file> in the browser's language whatever the
page says, so a Persian user met English beside every upload. The fields are
dressed (dressFileInputs in js/03-ui.js) — and stay the real inputs: still
what is clicked, what set_input_files fills, what a script clears."""
from __future__ import annotations

from tests_e2e.conftest import switch_language


def _pick(page, input_id):
    return page.evaluate("""(id) => { const i = document.getElementById(id), w = i.closest('.file-pick');
        if (!w) return null;
        const r = w.querySelector('.file-pick-btn').getBoundingClientRect();
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { btn: w.querySelector('.file-pick-btn').textContent, name: w.querySelector('.file-pick-name').textContent,
                 onTop: top === i }; }""", input_id)


def test_the_file_fields_speak_persian(flow_page):
    page, watch = flow_page("e2e_files")
    try:
        switch_language(page, "fa")
        page.click('.nav-btn[data-page="bank-statements"]')
        page.wait_for_function("() => document.getElementById('bs-file-input').closest('.file-pick')", timeout=5_000)
        assert _pick(page, "bs-file-input") == {"btn": "انتخاب فایل", "name": "فایلی انتخاب نشده", "onTop": True}

        page.set_input_files("#bs-file-input", files=[{"name": "mellat-mehr.csv", "mimeType": "text/csv", "buffer": b"date,amount\n"}])
        assert _pick(page, "bs-file-input")["name"] == "mellat-mehr.csv"
        page.evaluate("() => { document.getElementById('bs-file-input').value = ''; }")   # what a script does after an upload
        assert _pick(page, "bs-file-input")["name"] == "فایلی انتخاب نشده"

        page.click('.nav-btn[data-page="transactions"]')
        page.wait_for_function("() => document.getElementById('attachment-input').closest('.file-pick')", timeout=5_000)
        receipts = [{"name": f"receipt-{i}.png", "mimeType": "image/png", "buffer": b"\x89PNG\r\n\x1a\n"} for i in (1, 2)]
        page.set_input_files("#attachment-input", files=receipts)
        assert _pick(page, "attachment-input") == {"btn": "انتخاب فایل‌ها", "name": "2 فایل", "onTop": True}

        # every file field on screen is dressed, unless a script opens it from its own button
        bare = page.evaluate("""() => [...document.querySelectorAll('input[type=file]')]
            .filter(i => !i.closest('.file-pick') && i.style.display !== 'none' && i.offsetParent !== null).map(i => i.id)""")
        assert bare == []

        switch_language(page, "en")   # a switch repaints what is already on screen
        # the observer repaints a frame later, and the sidebar slides across as the
        # layout turns left to right: wait until the chip is clear again
        for _ in range(50):
            if _pick(page, "attachment-input") == {"btn": "Choose files", "name": "2 files", "onTop": True}:
                break
            page.wait_for_timeout(100)
        assert _pick(page, "attachment-input") == {"btn": "Choose files", "name": "2 files", "onTop": True}
        assert watch.problems() == [], watch.problems()
    finally:
        try:
            switch_language(page, "en")
        except Exception:   # the failure above is the one to report
            pass
