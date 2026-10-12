"""A hand-kept Excel journal on the import page (scenarios P1, P4, P7,
2026-10-12): the journal sheet of a workbook is read, the other sheets can be
picked, vouchers typed in another year wait for «use the file's year», and an
account the chart lacks defaults to «create». Preview only: the shared e2e
company's books are never touched. The workbook is a made-up school's."""
from __future__ import annotations

import os
from pathlib import Path

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until

BOOK = str(Path(__file__).parent / "fixtures" / "school_journal.xlsx")


def test_the_import_page_reads_a_schools_workbook(flow_page):
    page, watch = flow_page("e2e_handbook")
    switch_language(page, "fa")
    page.evaluate("() => { location.hash = 'migration'; }")
    page.wait_for_load_state("networkidle")
    page.set_input_files("#ji-file", BOOK)
    with page.expect_response(lambda r: "/migration/journals/preview" in r.url):
        page.click("#ji-preview-btn")
    wait_until(page, "() => document.getElementById('ji-sheet')")
    sheets = page.evaluate("() => [...document.querySelectorAll('#ji-sheet option')].map(o => [o.value, o.selected])")
    assert [s for s, _sel in sheets] == ["صورتحساب بانک", "کدینگ حسابها", "تراز", "دفتر روزنامه 1404"]
    assert dict(sheets)["دفتر روزنامه 1404"] is True                          # the journal, not the first sheet
    years = page.locator("[data-ji-years=fix]")
    assert years.is_visible() and "۱۴۰۴" in years.inner_text()                # two vouchers typed 1401 and 1403
    tuition = page.locator("#ji-result tr", has_text="درآمد شهریه").locator("select")
    chosen = tuition.evaluate("(s) => s.options[s.selectedIndex].textContent")
    assert chosen.startswith("ساختن «درآمد شهریه» زیر"), chosen              # created under revenue by default
    bank = page.locator("#ji-result tr", has_text="بانک ملت جاری").locator("select")
    assert "بانک تازه" in bank.evaluate("(s) => s.options[s.selectedIndex].textContent")
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, "hand-kept-journal.png"), full_page=True)

    with page.expect_response(lambda r: "/migration/journals/review" in r.url) as resp:
        years.click()
    assert "out_of_year" not in resp.value.json()["counts"]
    wait_until(page, "() => !document.querySelector('[data-ji-years=fix]') && document.querySelector('[data-ji-years=\"\"]')")

    with page.expect_response(lambda r: "/migration/journals/review" in r.url) as resp:
        page.select_option("#ji-sheet", "صورتحساب بانک")
    assert resp.value.json()["sheet"] == "صورتحساب بانک"                     # any sheet can be read
    assert watch.problems() == [], watch.problems()
