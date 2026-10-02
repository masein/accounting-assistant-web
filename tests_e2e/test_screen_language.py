"""What the pages draw from the server's raw values reads in the user's
language: an invoice's kind and status ("sales", "issued"), a party's type in
the invoice form ("client: …"), the voucher confirmation ("Date: 2026-09-29 …
Debit entries: 6112: …", Gregorian, codes without names) — deep browser test,
2026-10-02."""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until
from tests_e2e.test_jalali_dates import POST, _owner

LEAVES = r"""async () => { const r = await fetch('/accounts'); const a = await r.json();
  const parents = new Set(a.map(x => x.parent_id).filter(Boolean));
  return a.filter(x => x.is_active !== false && !parents.has(x.id) && x.name).slice(0, 2).map(x => [x.code, x.name]); }"""


def test_invoices_parties_and_the_voucher_check_read_in_persian(browser, flow_page):
    octx, owner = _owner(browser)
    page = None
    try:
        assert owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "jalali"}])[0] == 200
        page, watch = flow_page("e2e_screen")
        switch_language(page, "fa")
        run = uuid.uuid4().hex[:5]
        status, body = page.evaluate(POST, ["/entities", {"type": "client", "name": f"شرکت آزمون {run}"}])
        assert status in (200, 201), body
        today = date.today()
        status, body = page.evaluate(POST, ["/invoices", {"number": f"SCR-{run}", "kind": "sales", "status": "issued",
                                                          "amount": 1000, "issue_date": today.isoformat(),
                                                          "due_date": (today + timedelta(days=30)).isoformat()}])
        assert status == 201, body
        page.reload()
        page.wait_for_load_state("networkidle")

        page.click('.nav-btn[data-page="invoices"]')
        row = page.locator("#invoices-tbody tr", has_text=f"SCR-{run}")
        row.wait_for()
        cells = row.locator("td").all_inner_texts()
        assert cells[1] == "فروش" and cells[2] == "صادرشده", cells[:3]
        party = [o for o in page.locator("#inv-entity option").all_inner_texts() if f"آزمون {run}" in o]
        assert party == [f"شرکت آزمون {run} — مشتری"], party

        page.click('.nav-btn[data-page="transactions"]')
        (dr_code, dr_name), (cr_code, cr_name) = page.evaluate(LEAVES)
        page.fill("#description", "آزمون تأیید سند")
        rows = page.locator("#lines-tbody .line-row")
        rows.nth(0).locator(".line-code").fill(dr_code)
        rows.nth(0).locator(".line-debit").fill("2500")
        rows.nth(1).locator(".line-code").fill(cr_code)
        rows.nth(1).locator(".line-credit").fill("2500")
        page.click("#submit-btn")
        wait_until(page, "() => getComputedStyle(document.getElementById('ui-confirm-modal')).display !== 'none'"
                         " && document.getElementById('ui-confirm-title').textContent === t('confirmSaveVoucherTitle')")
        said = page.inner_text("#ui-confirm-message")
        shown_date = page.evaluate("() => formatDisplayDate(document.getElementById('date').value)")
        try:
            assert re.fullmatch(r"\u2066?1[34]\d\d/\d\d/\d\d\u2069?", shown_date), shown_date
            assert said.startswith("تاریخ: " + shown_date), said
            assert "بدهکار:" in said and "بستانکار:" in said and "جمع:" in said, said
            assert f"{dr_code} {dr_name}" in said and f"{cr_code} {cr_name}" in said, said
            assert not re.search(r"\b(Date|Debit|Credit|Total|Currency)\b", said), said
            assert not re.search(r"\b20\d\d-\d\d-\d\d\b", said), said
            os.makedirs(ARTIFACTS, exist_ok=True)
            page.screenshot(path=os.path.join(ARTIFACTS, "voucher-confirm-fa.png"))
        finally:
            page.click("#ui-confirm-cancel")

        # the general journal's dates are in the calendar too (it printed 2026-09-30)
        status, body = page.evaluate(POST, ["/transactions", {"date": today.isoformat(), "description": "آزمون دفتر روزنامه",
                                                              "lines": [{"account_code": dr_code, "debit": 700, "credit": 0},
                                                                        {"account_code": cr_code, "debit": 0, "credit": 700}]}])
        assert status in (200, 201), body
        page.click('.nav-btn[data-page="manager"]')
        page.select_option("#mgr-report-type", "general_journal")
        page.evaluate("""([a, b]) => { for (const [id, v] of [['mgr-from-date', a], ['mgr-to-date', b]]) {
            const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); } }""",
                      [(today - timedelta(days=7)).isoformat(), today.isoformat()])
        with page.expect_response(lambda r: "/general-journal" in r.url):
            page.click("#mgr-run-btn")
        wait_until(page, "() => document.querySelector('#mgr-report-preview table')")
        journal = page.inner_text("#mgr-report-preview")
        assert "آزمون دفتر روزنامه" in journal and shown_date.strip("\u2066\u2069") in journal, journal[:400]
        assert not re.search(r"\b20\d\d-\d\d-\d\d\b", journal), journal[:400]
        assert watch.problems() == [], watch.problems()
    finally:
        if page is not None:
            try:
                switch_language(page, "en")
            except Exception:
                pass
        owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "gregorian"}])
        octx.close()
