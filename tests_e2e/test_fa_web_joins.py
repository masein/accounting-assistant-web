"""The web app's Persian in a browser (Persian copy review, 2026-10-31; the source checks are
tests/test_fa_web.py): pieces join with «، » (the middle dot beside Persian digits read as a zero,
«امروز · ۱۸ مهر» → «۱۸۰ مهر»), a range of dates reads «… تا …», the ledger's group rows name each
parent's own tier on the chart, and the figures the page writes itself are in Persian digits.
English pages keep « · » and the left-to-right range; Arabic joins with «، » and says «إلى»."""
from __future__ import annotations

import os
import re
import uuid

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until
from tests_e2e.test_persian_labels import POST_JOURNALS

HELPERS = r"""() => ({
  sep: partSep(), parts: joinParts(['امروز', '', '۱۸ مهر']), list: joinList(['علی رضایی', 'سارا محمدی']),
  range: formatDateRange('2026-10-01', '2026-10-31'),
  rangeWant: '⁦' + formatDisplayDate('2026-10-01') + '⁩ تا ⁦' + formatDisplayDate('2026-10-31') + '⁩',
  when: formatDisplayDateTime(new Date(2026, 9, 31, 11, 1)), zero: chartTick(0), big: chartTick(-2500000),
  assets: assetMoneyByCurrency({}, 'cost'),
})"""
GROUP_ROWS = r"""async () => {
  const accs = await (await fetch('/accounts?include_inactive=true&limit=500')).json();
  const level = Object.fromEntries(accs.map(a => [a.code, a.level]));
  return [...document.querySelectorAll('#results-tbody tr.ledger-group-row')].map(tr => {
    const code = latinDigits(tr.children[0].textContent.trim());
    const tag = tr.children[1].querySelector('span');
    return { code, want: level[code] ? '(' + t('coaLevel_' + level[code]) + ')' : '', tag: tag ? tag.textContent.trim() : '' };
  });
}"""
LATIN = re.compile(r"[0-9]")


def test_persian_pieces_ranges_and_tiers_read_right(flow_page):
    page, watch = flow_page("e2e_fajoins")
    assert all(s < 300 for s in page.evaluate(POST_JOURNALS, f"FAJ-{uuid.uuid4().hex[:6]}"))
    switch_language(page, "fa")
    got = page.evaluate(HELPERS)
    assert (got["sep"], got["parts"], got["list"]) == ("، ", "امروز، ۱۸ مهر", "علی رضایی، سارا محمدی")
    assert got["range"] == got["rangeWant"], got                     # start first, in the page's direction
    assert got["when"].endswith(" ۱۱:۰۱") and not LATIN.search(got["when"]), got["when"]
    assert got["zero"] == "۰" and "۲٬۵۰۰٬۰۰۰" in got["big"] and not LATIN.search(got["big"])   # Chart.js wrote «0»
    assert not LATIN.search(got["assets"]), got["assets"]           # the fixed-asset KPIs said «0»

    # the ledger: each group row tagged with its parent's tier on the chart, never «دفتر کل» for all
    page.evaluate("() => { location.hash = 'ledger'; }")
    wait_until(page, "() => document.querySelectorAll('#results-tbody tr.ledger-group-row').length > 0", timeout_ms=10_000)
    rows = page.evaluate(GROUP_ROWS)
    assert rows and all(r["tag"] == r["want"] for r in rows), rows
    assert not any(r["tag"] == "(دفتر کل)" for r in rows), rows
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, "fa-web-ledger-tiers.png"), full_page=True)
    # the drawer: both codes in Persian digits, each with its own tier
    page.evaluate("() => openAccountDetail('1110')")
    wait_until(page, "() => (document.querySelector('#account-modal-body p') || {}).textContent")
    line = page.inner_text("#account-modal-body p")
    assert "۱۱۱۰" in line and "1110" not in line and "دفتر" not in line and "·" not in line, line
    page.evaluate("() => { document.getElementById('account-modal').style.display = 'none'; }")

    # the audit trail's time in Persian digits («۲۰۲۶-۱۰-۳۱ 11:01»)
    page.evaluate("() => { location.hash = 'audit'; }")
    wait_until(page, "() => document.querySelectorAll('#audit-log-body tr').length > 0", timeout_ms=10_000)
    when = page.inner_text("#audit-log-body tr:first-child td:first-child")
    assert when and not LATIN.search(when), when
    # the conversations list: «۵ دقیقه پیش», not «5 دقیقه پیش» (_relTime is the chat's own; its text is what shows)
    assert page.evaluate("() => localDigits(5) + ' ' + t('chatTimeMin')") == "۵ دقیقه پیش"

    # Arabic: «، » too, and «إلى» between the dates
    switch_language(page, "ar")
    ar = page.evaluate("() => [partSep(), joinList(['a', 'b']), formatDateRange('2026-10-01', '2026-10-31')]")
    assert ar[:2] == ["، ", "a، b"] and " إلى " in ar[2], ar
    # English is as it was: « · », ", " and one left-to-right run with its arrow
    switch_language(page, "en")
    en = page.evaluate("() => [partSep(), joinList(['a', 'b']), formatDateRange('2026-10-01', '2026-10-31'),"
                       " '\\u2066' + formatDisplayDate('2026-10-01') + ' → ' + formatDisplayDate('2026-10-31') + '\\u2069']")
    assert en[:2] == [" · ", "a, b"] and en[2] == en[3], en
    assert watch.problems() == [], watch.problems()
