"""The journal as the tax office's Excel sheet (scenarios BL1, BL7): «دفتر روزنامه (اکسل)» shows for
the journal only, takes the page's dates and currency without running the report, and downloads the
owner's columns, a row per line."""
from __future__ import annotations

import io
import os
import uuid

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until
from tests_e2e.test_jalali_dates import POST

HEADER = ["ردیف", "تاریخ", "کد حساب کل", "عنوان حساب کل", "کد حساب معین", "عنوان حساب معین", "شرح",
          "مبلغ بدهکار (ریال)", "مبلغ بستانکار (ریال)"]


def test_the_journal_downloads_as_the_tax_offices_sheet(flow_page):
    from openpyxl import load_workbook
    page, watch = flow_page("e2e_jxlsx")
    try:
        switch_language(page, "fa")
        words = f"اجاره {uuid.uuid4().hex[:6]}"
        status, body = page.evaluate(POST, ["/transactions", {"date": "2026-05-10", "description": words, "currency": "IRR",
            "lines": [{"account_code": "6112", "debit": 4_500_000, "credit": 0},
                      {"account_code": "1110", "debit": 0, "credit": 4_500_000}]}])
        assert status == 201, body
        page.click('.nav-btn[data-page="manager"]')
        button = page.locator("#mgr-journal-xlsx-btn")
        page.select_option("#mgr-report-type", "balance_sheet")
        assert not button.is_visible()                      # the journal's alone
        page.select_option("#mgr-report-type", "general_journal")
        wait_until(page, "() => !document.getElementById('mgr-journal-xlsx-btn').hidden")
        assert button.inner_text() == "دفتر روزنامه (اکسل)"
        page.fill("#mgr-from-date", "2026-05-01")
        page.fill("#mgr-to-date", "2026-05-31")
        page.select_option("#mgr-currency", "IRR")
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator('.card[data-page="manager"]').first.screenshot(path=os.path.join(ARTIFACTS, "journal-excel-button.png"))
        with page.expect_download() as dl:                  # not run first: the button asks the server
            button.click()
        assert dl.value.suggested_filename.endswith(".xlsx")
        ws = load_workbook(io.BytesIO(open(dl.value.path(), "rb").read())).worksheets[0]   # the path has no .xlsx
        assert ws.sheet_view.rightToLeft
        assert [c.value for c in ws[1]][:9] == HEADER
        mine = [[c.value for c in r[:9]] for r in ws.iter_rows(min_row=2) if r[6].value == words]
        assert [r[7:] for r in mine] == [[4_500_000, 0], [0, 4_500_000]]
        assert mine[0][0] == mine[1][0] and isinstance(mine[0][2], int)
        assert watch.problems() == [], watch.problems()
    finally:
        try:
            switch_language(page, "en")
        except Exception:
            pass
