"""A school's books switched to their own codes from the chart page (scenarios AN3–AN5).

The owner, 2026-10-21: «the coding is still wrong in دفتر کل». The server's preview is played here as
فرهنگ نو's real files give it (tests/test_own_codes_switch.py imports them): 3000 «هزینه های عمومی» with
3001 under it, «وزارت دارایی» and «دارایی» one account (1602), the app's unused 1110 put aside for the
school's petty cash. The page shows it, lets a code be changed, and sends the codes and the twins on the
owner's word."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

PREVIEW = {
    "accounts": [
        {"id": "a1", "code": "6131", "name": "هزینه های عمومی", "level": "GENERAL", "lines": 13, "options": ["3000"], "proposed": "3000", "merged_into": None},
        {"id": "a2", "code": "613104", "name": "پذیرایی از همکاران و اولیا ونظافت طی ماه", "level": "SUB", "lines": 12, "options": ["3001"], "proposed": "3001", "merged_into": None},
        {"id": "a3", "code": "613106", "name": "پذیرایی از همکاران و اولیا ونظافت", "level": "SUB", "lines": 2, "options": ["3001"], "proposed": "3001", "merged_into": "a2"},
        {"id": "a4", "code": "219602", "name": "وزارت دارایی", "level": "SUB", "lines": 23, "options": ["1602"], "proposed": "1602", "merged_into": None},
        {"id": "a5", "code": "219604", "name": "دارایی", "level": "SUB", "lines": 3, "options": ["1602"], "proposed": "1602", "merged_into": "a4"},
        {"id": "a6", "code": "1151", "name": "تنخواه گردان", "level": "GENERAL", "lines": 44, "options": ["1110"], "proposed": "1110", "merged_into": None},
        {"id": "a7", "code": "6133", "name": "هزینه اجاره", "level": "GENERAL", "lines": 16, "options": ["131000"], "proposed": "131000", "merged_into": None},
        {"id": "a8", "code": "3300", "name": "سود (زیان) انباشته", "level": "GENERAL", "lines": 0, "options": ["4000", "5000"], "proposed": "5000", "merged_into": None},
        {"id": "a9", "code": "1110", "name": "موجودی نقد و بانک", "level": "GENERAL", "lines": 0, "options": [], "proposed": None, "merged_into": None},
    ],
    "merges": [{"code": "3001", "keep": "a2", "merge": ["a3"]}, {"code": "1602", "keep": "a4", "merge": ["a5"]}],
    "clashes": [], "aside": [{"id": "a9", "code": "1110", "new_code": "11100"}], "ready": True,
    "counts": {"recoded": 6, "merged": 2, "aside": 1, "clashes": 0},
}


def test_an3_the_chart_page_switches_to_the_schools_codes(flow_page):
    page, watch = flow_page("e2e_owncodes")
    sent = []
    state = {"switched": False}

    def preview(route):
        if route.request.method == "POST":
            sent.append(json.loads(route.request.post_data or "{}"))
            state["switched"] = True
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"recoded": 6, "merged": 2, "lines_moved": 5, "cards_cancelled": 0, "moved": {}}))
        elif state["switched"]:
            route.fulfill(status=409, content_type="application/json",
                          body=json.dumps({"detail": "These books already use their own codes."}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(PREVIEW, ensure_ascii=False))
    page.route("**/accounts/own-codes", preview)
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'accounts'; }")
        page.wait_for_load_state("networkidle")
        panel = page.locator("#coa-own-panel")
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("کدهای حساب خودتان")
        expect(page.locator("#coa-own-summary")).to_contain_text("۶ حساب کد شما را می‌گیرد")
        rows = page.locator(".coa-own tr")
        expect(rows.filter(has_text="دارایی").filter(has_text="همان حساب «وزارت دارایی»")).to_have_count(1)
        expect(rows.filter(has_text="موجودی نقد و بانک")).to_contain_text("استفاده نشده")
        expect(page.locator("input[data-account='a8']")).to_have_value("5000")
        expect(rows.filter(has_text="سود (زیان) انباشته")).to_contain_text("۴۰۰۰")              # its other code, said
        page.fill("input[data-account='a7']", "1310")                                           # rent as its ledger
        os.makedirs(ARTIFACTS, exist_ok=True)
        panel.screenshot(path=os.path.join(ARTIFACTS, "own-codes-preview-fa.png"))
        page.click("#coa-own-apply")
        page.click("#ui-confirm-ok")
        expect(page.locator("#alert")).to_contain_text("دفاتر شما حالا کدهای خودشان را دارند")
        expect(panel).to_be_hidden()
        [body] = sent
        assert body["codes"] == {"a1": "3000", "a2": "3001", "a4": "1602", "a6": "1110", "a7": "1310", "a8": "5000",
                                 "a9": "11100"}
        assert body["merges"] == [{"keep": "a2", "merge": ["a3"]}, {"keep": "a4", "merge": ["a5"]}]
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
